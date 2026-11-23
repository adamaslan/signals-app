import { test, expect } from '@playwright/test';

test('recipe input edits clear the prior engine output before a rerun', async ({ page }) => {
  await page.goto('/signals-app/recipes/');
  await expect(page.getByRole('heading', { name: 'Scan recipe workbench' })).toBeVisible();
  await page.getByRole('button', { name: 'Load invented demo input' }).click();
  await page.getByRole('button', { name: 'Run selected recipe or method' }).click();
  await expect(page.getByTestId('recipe-output')).toBeVisible();

  await page.getByLabel('Input JSON, stays in this browser').fill('{');
  await expect(page.getByTestId('recipe-output')).toHaveCount(0);
  await expect(page.getByTestId('synthetic-label')).toHaveCount(0);

  await page.getByRole('button', { name: 'Run selected recipe or method' }).click();
  await expect(page.getByTestId('recipe-output')).toContainText('"status": "missing"');
  await page.getByRole('button', { name: 'Load invented demo input' }).click();
  await expect(page.getByTestId('recipe-output')).toHaveCount(0);
  await expect(page.getByTestId('synthetic-label')).toBeVisible();
});
