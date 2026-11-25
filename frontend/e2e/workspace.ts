import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { test as base } from '@playwright/test';

export const root = fileURLToPath(new URL('../../', import.meta.url));
export const examples = join(root, 'examples');

// Each case gets a unique ledger, media directory and loopback port.
export const test = base.extend<{ seedExamples: boolean }>({
  seedExamples: [true, { option: true }],
  baseURL: async ({ seedExamples }, use) => {
    const data = await mkdtemp(join(tmpdir(), 'echo-browser-'));
    const args = ['-m', 'server', '--data-dir', data, '--port', '0'];
    if (seedExamples) args.push('--seed-examples');
    const child = spawn(process.env.ECHO_TEST_PYTHON || 'python3', args, { cwd: root });
    const done = once(child, 'close');
    const lines = createInterface({ input: child.stdout });
    let errors = '';
    child.stderr.on('data', (chunk) => {
      errors += chunk;
    });
    try {
      for await (const line of lines) {
        const { url } = JSON.parse(line);
        if (new URL(url).hostname !== '127.0.0.1') throw new Error('Expected an isolated local service');
        await use(url);
        return;
      }
      throw new Error('Echo stopped before serving the test workspace. ' + errors);
    } finally {
      lines.close();
      child.kill();
      await done;
      await rm(data, { recursive: true, force: true });
    }
  },
});
