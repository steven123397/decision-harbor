import { expect, test } from '@playwright/test'

test('allowed query shows a result table', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('sql-input').fill('SELECT id, display_name FROM customers LIMIT 5')
  await page.getByTestId('submit').click()
  await expect(page.getByTestId('result-table')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('status')).toContainText('成功')
})

test('rejected query shows a rejection reason', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('sql-input').fill('DROP TABLE customers')
  await page.getByTestId('submit').click()
  await expect(page.getByTestId('error')).toContainText('POLICY_FORBIDDEN_STATEMENT', { timeout: 10_000 })
})
