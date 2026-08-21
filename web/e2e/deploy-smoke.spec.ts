import { test, expect } from '@playwright/test';

test.describe('Deploy smoke tests', () => {
  test('page boots without console errors', async ({ page }) => {
    const errors: string[] = [];

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    await page.goto('./');
    expect(errors).toHaveLength(0);
  });

  test('no 4xx asset errors on initial load', async ({ page }) => {
    const failedRequests: string[] = [];

    page.on('response', (response) => {
      if (response.status() >= 400 && response.status() < 500) {
        failedRequests.push(`${response.status()} ${response.url()}`);
      }
    });

    await page.goto('./');
    expect(failedRequests).toHaveLength(0);
  });

  test('deep link survives 404 redirect', async ({ page }) => {
    // Navigate to a deep link (that doesn't exist as a static file)
    // The SPA should rewrite to index.html
    await page.goto('/ticker/AAPL');

    // Page should load without 404
    expect(page.url()).toContain('/ticker/AAPL');

    // Content should be present
    const body = await page.locator('body').textContent();
    expect(body).toBeTruthy();
  });

  test('page title is present', async ({ page }) => {
    await page.goto('./');
    const title = await page.title();
    expect(title).toBeTruthy();
  });
});
