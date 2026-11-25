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

test('phone recipe filters wrap and search remains usable', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/signals-app/recipes/');
  await expect(page.getByRole('button', {name:'Catalog recipes',exact:true})).toBeVisible();
  const search=page.getByRole('textbox',{name:'Search recipes'});
  const box=await search.boundingBox();
  expect(box?.width ?? 0).toBeGreaterThanOrEqual(180);
  await search.fill('underwater');
  await expect(page.getByText('100 recipes match.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Reset',exact:true}).click();
  await expect(search).toHaveValue('');
  await expect(page.getByText('1000 recipes match.',{exact:false})).toBeVisible();
  await expect(page.getByText(/Four five-session underwater-age stress definitions remain unavailable/)).toBeVisible();
});
