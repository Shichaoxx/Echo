import { copyFile, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect } from '@playwright/test';
import { examples, test as workspaceTest } from './workspace';

const first = '星河 一面 2026 年 11 月 08 日';
const second = '远山 二面 2026-11-12';

const test = workspaceTest.extend<{ interviews: string }>({
  interviews: async ({}, use) => {
    const root = await mkdtemp(join(tmpdir(), 'echo-interviews-'));
    try {
      await mkdir(join(root, first));
      await mkdir(join(root, second));
      await copyFile(join(examples, 'reading-room/audio.mp3'), join(root, first, '录音.mp3'));
      await copyFile(join(examples, 'night-bus/audio.mp3'), join(root, second, '录音.mp3'));
      await writeFile(join(root, first, '面试笔记.txt'), '问题记录');
      await writeFile(join(root, '目录说明.md'), '# 面试材料');
      await use(root);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  },
});

test.use({ seedExamples: false });

test('imports one interview folder and then its parent without duplicating the recording', async ({
  page,
  interviews,
}) => {
  await page.goto('/#/library');
  const open = page.getByRole('button', { name: '导入录音', exact: true });
  const dialog = page.getByRole('dialog', { name: '导入录音' });
  const picker = dialog.locator('input[type=file][webkitdirectory]');
  const submit = dialog.getByRole('button', { name: /^导入所选/ });
  const entries = page.getByRole('main').getByRole('listitem');

  await open.click();
  await picker.setInputFiles(join(interviews, first));
  await expect(dialog.getByRole('checkbox')).toHaveCount(1);
  await expect(dialog.getByRole('checkbox', { name: first, exact: true })).toBeChecked();
  await submit.click();
  await expect(dialog).toBeHidden();
  await expect(entries).toHaveCount(1);
  const firstLink = page.getByRole('link', { name: new RegExp(first) });
  await expect(firstLink.locator('time')).toHaveAttribute('datetime', '2026-11-08');
  const firstHref = await firstLink.getAttribute('href');
  expect(firstHref).toBeTruthy();

  await open.click();
  await picker.setInputFiles(interviews);
  await expect(dialog.getByRole('checkbox')).toHaveCount(2);
  for (const name of [first, second]) {
    await expect(dialog.getByRole('checkbox', { name, exact: true })).toBeChecked();
  }
  await submit.click();
  await expect(dialog).toBeHidden();
  await expect(entries).toHaveCount(2);
  await page.reload();
  await expect(entries).toHaveCount(2);
  await expect(firstLink).toHaveAttribute('href', firstHref!);
  await expect(firstLink.locator('time')).toHaveAttribute('datetime', '2026-11-08');
  const secondLink = page.getByRole('link', { name: new RegExp(second) });
  await expect(secondLink.locator('time')).toHaveAttribute('datetime', '2026-11-12');
});

test('retries only failed uploads and leaves unchecked interviews out of the library', async ({
  page,
  interviews,
}) => {
  const excluded = '未选面试';
  await mkdir(join(interviews, excluded));
  await copyFile(join(examples, 'reading-room/audio.mp3'), join(interviews, excluded, '录音.mp3'));

  const attempts: string[] = [];
  await page.route('**/api/recordings?*', async (route) => {
    const title = new URL(route.request().url()).searchParams.get('title')!;
    const failOnce = title === first && !attempts.includes(first);
    attempts.push(title);
    if (failOnce) {
      await route.fulfill({ status: 503, json: { error: '测试服务暂时不可用' } });
    } else {
      await route.continue();
    }
  });

  await page.goto('/#/library');
  await page.getByRole('button', { name: '导入录音', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导入录音' });
  await dialog.locator('input[type=file][webkitdirectory]').setInputFiles(interviews);
  await dialog.getByRole('checkbox', { name: excluded, exact: true }).uncheck();
  await dialog.getByRole('button', { name: /^导入所选/ }).click();

  const retry = dialog.getByRole('button', { name: /^重试所选/ });
  await expect(retry).toBeEnabled();
  await expect(dialog.getByRole('alert')).toContainText('导入失败');
  await expect(dialog.getByRole('checkbox', { name: second, exact: true })).toBeDisabled();
  expect([...attempts].sort()).toEqual([first, second].sort());
  await retry.click();
  await expect(dialog).toBeHidden();
  const entries = page.getByRole('main').getByRole('listitem');
  await expect(entries).toHaveCount(2);
  for (const title of [first, second]) {
    await expect(entries.filter({ hasText: title })).toHaveCount(1);
  }
  expect([...attempts].sort()).toEqual([first, first, second].sort());
});
