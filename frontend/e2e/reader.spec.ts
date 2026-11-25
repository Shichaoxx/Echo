import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { expect, type Page } from '@playwright/test';
import { examples, test } from './workspace';

async function openExample(page: Page, title: string) {
  await page.goto('/#/library');
  await page.getByRole('link', { name: new RegExp(title) }).click();
  await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
}

async function options(page: Page) {
  await page.getByLabel('文档操作').click();
}

test('a follow-up on the overview opens its cited source and plays the pinned interval', async ({ page }) => {
  await openExample(page, '夜班公交的到站提醒');
  await expect(page.locator('#ed-chapter > .ed-block')).toHaveCount(3);
  const id = new URL(page.url()).hash.split('/').at(-1);
  const detailPath = '/api/recordings/' + id;
  const pinnedRun = (await (await page.request.get(detailPath)).json()).document.source_run_id;
  await options(page);
  await page.getByRole('button', { name: '转写与录音', exact: true }).click();
  await page.getByRole('button', { name: '导入旧转写' }).click();
  await page.getByLabel('转写 JSON').setInputFiles(join(examples, 'night-bus/source.json'));
  await page.getByRole('button', { name: '确认导入' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  const updated = await (await page.request.get(detailPath)).json();
  expect(updated.runs).toHaveLength(2);
  expect(updated.runs[0].id).not.toBe(pinnedRun);
  const followup = page.locator('#ed-block-data #ed-block-turnback');
  await page.locator('.ed-board').getByRole('button', { name: '时间戳很新，车却折返了呢？' }).click();
  await expect(followup.locator('details')).toHaveAttribute('open', '');
  const proof = followup.locator('.ed-source').filter({ hasText: '我追问你一个反例' });
  // Headless Chromium can lose OS audio focus. Muting the output still exercises real MP3 decoding.
  await page.locator('audio').evaluate((el: HTMLAudioElement) => {
    el.muted = true;
  });
  await proof.getByRole('button').click();
  const source = JSON.parse(await readFile(join(examples, 'night-bus/source.json'), 'utf8'));
  const interval = source.result.segments.find((s: { text: string }) => s.text.includes('我追问你一个反例'));
  const player = page.locator('audio');
  await expect
    .poll(() =>
      player.evaluate((audio: HTMLAudioElement) => ({
        run: new URL(audio.currentSrc, document.baseURI).searchParams.get('run'),
        playing: !audio.paused && audio.currentTime > 0,
      })),
    )
    .toEqual({ run: pinnedRun, playing: true });
  const time = await player.evaluate((audio: HTMLAudioElement) => audio.currentTime);
  expect(time).toBeGreaterThanOrEqual(interval.start - 0.1);
  expect(time).toBeLessThan(interval.end);
  await expect.poll(() => player.evaluate((audio: HTMLAudioElement) => audio.paused)).toBe(true);
  const stoppedAt = await player.evaluate((audio: HTMLAudioElement) => audio.currentTime);
  expect(stoppedAt).toBeGreaterThanOrEqual(interval.end - 0.1);
  expect(stoppedAt).toBeLessThan(interval.end + 0.8);
});

test('chapter and follow-up headings play the discussion past its first cited sentence', async ({ page }) => {
  await openExample(page, '夜班公交的到站提醒');
  const data = join(examples, 'night-bus');
  const source = JSON.parse(await readFile(join(data, 'source.json'), 'utf8'));
  const minutes = JSON.parse(await readFile(join(data, 'document.json'), 'utf8'));
  function firstSegment(blockId: string): { start: number; end: number } {
    const block = minutes.blocks.find((b: { id: string }) => b.id === blockId);
    return source.result.segments[block.evidence[0]];
  }

  const player = page.locator('audio');
  await player.evaluate((el: HTMLAudioElement) => {
    el.muted = true;
  });
  const index = page.getByRole('complementary', { name: '录音章节索引' });
  await index.getByRole('button', { name: /回听章节.*哪一次车辆上报/ }).click();
  const chapter = page.getByRole('contentinfo', { name: '章节回听' });
  await expect(chapter.getByRole('slider')).toHaveAttribute('min', String(firstSegment('data').start));
  await expect(page.locator('#ed-block-data > h3')).toBeInViewport();
  await page.locator('#ed-block-data > h3').getByRole('button').click();
  await expect(chapter.getByRole('slider')).toHaveAttribute('min', String(firstSegment('data').start));
  await expect(chapter.getByRole('slider')).toHaveAttribute('max', String(firstSegment('trial').start));

  await page.locator('#ed-block-turnback > h4').getByRole('button').click();
  const followup = page.getByRole('contentinfo', { name: '追问回听' });
  await expect(followup.getByText('追问回听', { exact: true })).toBeVisible();
  const first = firstSegment('turnback');
  await expect(followup.getByRole('slider')).toHaveAttribute('min', String(first.start));
  await expect(followup.getByRole('slider')).toHaveAttribute('max', String(firstSegment('recovery').start));
  await expect
    .poll(() => player.evaluate((el: HTMLAudioElement) => (el.paused ? 0 : el.currentTime)))
    .toBeGreaterThan(first.end + 0.15);
  await followup.getByRole('button', { name: '暂停回听' }).click();
  await expect.poll(() => player.evaluate((el: HTMLAudioElement) => el.paused)).toBe(true);
});

test('an edit survives reopening, while the original version still exports its own text', async ({
  page,
}) => {
  await openExample(page, '夜班公交的到站提醒');
  const original = await page.locator('#ed-block-brief > .ed-prose').innerText();
  const edited = original + ' 本次核对包括折返反例。';
  await page.getByRole('button', { name: '修订正文' }).click();
  await page.locator('#ed-block-brief').getByRole('textbox', { name: '正文', exact: true }).fill(edited);
  await page.getByRole('button', { name: '保存修订' }).click();
  await expect(page.locator('#ed-block-brief > .ed-prose')).toHaveText(edited);
  await page.reload();
  await expect(page.locator('#ed-block-brief > .ed-prose')).toHaveText(edited);
  await options(page);
  await page.getByLabel('历史版本').selectOption('1');
  await expect(page.locator('#ed-block-brief > .ed-prose')).toHaveText(original);
  await options(page);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出 Markdown' }).click();
  const file = await (await download).path();
  expect(file).toBeTruthy();
  const markdown = await readFile(file!, 'utf8');
  expect(markdown).toContain(original);
  expect(markdown).toContain('时间戳很新，车却折返了呢？');
  expect(markdown).not.toContain(edited);
});

test('the other recording has its own document and works at phone widths', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openExample(page, '阅览室的周末预约');
  await expect(page.locator('.ed-board h3')).toHaveText('从预约状态到现场入座');
  await page.locator('.ed-mobile summary').click();
  await page.locator('.ed-mobile').getByRole('button', { name: '智能章节' }).click();
  await expect(page.locator('#ed-chapter h2')).toBeInViewport();
  await expect(page.locator('#ed-block-grace h3')).toContainText('迟到的读者可以等多久？');
  const width = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    screen: innerWidth,
    reader: document.querySelector('.ed-scroll')!.scrollWidth,
    visible: document.querySelector('.ed-scroll')!.clientWidth,
  }));
  expect(width.page).toBeLessThanOrEqual(width.screen + 1);
  expect(width.reader).toBeLessThanOrEqual(width.visible + 1);
});

test('reduced motion keeps the library action still and returns keyboard focus after dismissal', async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.goto('/#/library');
  const trigger = page.getByRole('button', { name: '导入录音', exact: true });
  await trigger.scrollIntoViewIfNeeded();
  await trigger.hover();
  const rest = await trigger.boundingBox();
  expect(rest).toBeTruthy();
  await page.mouse.down();
  // Sample through the press to catch both transient springs and a steady scale.
  const displacement = await trigger.evaluate(async (button, initial) => {
    let max = 0;
    const start = performance.now();
    do {
      await new Promise(requestAnimationFrame);
      const { x, y, width, height } = button.getBoundingClientRect();
      max = Math.max(
        max,
        Math.abs(x - initial.x),
        Math.abs(y - initial.y),
        Math.abs(width - initial.width),
        Math.abs(height - initial.height),
      );
    } while (performance.now() - start < 180);
    return max;
  }, rest!);
  await page.mouse.up();
  expect(displacement).toBeLessThan(0.5);
  await expect(page.getByRole('dialog', { name: '导入录音' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(trigger).toBeFocused();
});

test.describe('an empty library', () => {
  test.use({ seedExamples: false });

  test('imports old ASR, exports a real Codex handoff and accepts a source-bound document', async ({
    page,
  }) => {
    await page.goto('/');
    await page.getByRole('button', { name: '选择录音' }).click();
    const recording = join(examples, 'reading-room');
    await page.getByLabel('录音或视频').setInputFiles(join(recording, 'audio.mp3'));
    await page.getByLabel('面试标题').fill('周末预约试行');
    await page.getByRole('button', { name: '导入并继续' }).click();
    await page.getByRole('button', { name: '导入旧转写' }).click();
    await page.getByLabel('转写 JSON').setInputFiles(join(recording, 'source.json'));
    await page.getByRole('button', { name: '确认导入' }).click();
    await page.getByRole('button', { name: '交给 Codex 生成' }).click();
    const prompt = page.getByLabel('交接内容');
    await expect(prompt).toHaveValue(/"transcript_sha256"/);
    const handoff = await prompt.inputValue();
    const packet = JSON.parse(handoff.slice(handoff.indexOf('\n{') + 1));
    expect(packet.segments.some((s: { text: string }) => s.text.includes('15分钟'))).toBe(true);
    const spec = JSON.parse(await readFile(join(recording, 'document.json'), 'utf8'));
    await page.getByRole('button', { name: '关闭', exact: true }).click();
    await page.getByRole('button', { name: '导入纪要', exact: true }).click();
    await page.getByLabel('纪要 JSON').setInputFiles({
      name: 'minutes.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify({ ...packet.template, ...spec })),
    });
    await page.getByRole('button', { name: '确认导入' }).click();
    await expect(page.locator('.echo-doc h1')).toHaveText('周末预约试行');
    await expect(page.locator('#ed-block-decision')).toContainText('靠窗十二席');
  });
});
