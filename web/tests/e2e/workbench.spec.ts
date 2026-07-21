import { test, expect } from '@playwright/test'

test('允许查询展示结果表格', async ({ page }) => {
  await page.goto('/')
  await page.fill(
    '#sql-input',
    'SELECT id, customer_code FROM analytics.customers ORDER BY id LIMIT 3',
  )
  await page.click('#submit-btn')
  await expect(page.getByTestId('status')).toContainText('succeeded')
  await expect(page.locator('#result table tbody tr')).toHaveCount(3)
})

test('禁止查询展示拒绝信息', async ({ page }) => {
  await page.goto('/')
  await page.fill('#sql-input', 'DELETE FROM analytics.customers')
  await page.click('#submit-btn')
  await expect(page.getByTestId('error-code')).toContainText('FORBIDDEN_STATEMENT')
})

test('多语句被拒绝', async ({ page }) => {
  await page.goto('/')
  await page.fill('#sql-input', 'SELECT 1; SELECT 2;')
  await page.click('#submit-btn')
  await expect(page.getByTestId('error-code')).toContainText('MULTI_STATEMENT')
})
