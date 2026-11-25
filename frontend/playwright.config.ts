import { existsSync } from 'node:fs';
import { defineConfig } from '@playwright/test';

const installedChannel =
  process.platform === 'darwin' &&
  (existsSync('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    ? 'chrome'
    : existsSync('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
      ? 'msedge'
      : undefined);

export default defineConfig({
  testDir: './e2e',
  workers: 2,
  use: {
    channel: process.env.ECHO_BROWSER_CHANNEL || installedChannel || undefined,
    locale: 'zh-CN',
    viewport: { width: 1440, height: 960 },
    headless: true,
  },
});
