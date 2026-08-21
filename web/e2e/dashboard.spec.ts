import { test, expect } from '@playwright/test';

test.describe('Dashboard functionality', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('Recent Runs section mounts', async ({ page }) => {
    // Look for a section that contains "Recent" or "Runs"
    const recentRunsSection = page.locator(
      'text=/recent|runs/i',
    ).first();

    await expect(recentRunsSection).toBeVisible({ timeout: 5000 });
  });

  test('Watchlist section mounts', async ({ page }) => {
    // Look for watchlist-related content
    const watchlistSection = page.locator(
      'text=/watchlist|portfolio/i',
    ).first();

    await expect(watchlistSection).toBeVisible({ timeout: 5000 });
  });

  test('ticker search accepts input', async ({ page }) => {
    // Find search input (look for input with placeholder or aria-label)
    const searchInput = page.locator('input[type="text"]').first();

    // Type a ticker symbol
    await searchInput.fill('AAPL');
    await expect(searchInput).toHaveValue('AAPL');

    // Clear it
    await searchInput.fill('');
    await expect(searchInput).toHaveValue('');
  });

  test('no console errors during interaction', async ({ page }) => {
    const errors: string[] = [];

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });

    // Try clicking on some elements (if they exist)
    const clickables = page.locator('button, a').first();
    if (await clickables.isVisible()) {
      await clickables.click().catch(() => {
        // Ignore click errors (element may not be clickable)
      });
    }

    expect(errors).toHaveLength(0);
  });
});
