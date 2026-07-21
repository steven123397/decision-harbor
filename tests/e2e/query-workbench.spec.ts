import { test, expect } from '@playwright/test'

test.describe('Query Workbench', () => {
  test('successful query shows result table', async ({ page }) => {
    await page.goto('/')
    const textarea = page.getByLabel('SQL 输入')
    await textarea.fill('SELECT region, COUNT(*) AS cnt FROM customers GROUP BY region ORDER BY cnt DESC LIMIT 3')
    await page.getByRole('button', { name: '执行查询' }).click()
    await expect(page.locator('table')).toBeVisible({ timeout: 15000 })
    await expect(page.locator('th').first()).toBeVisible()
    await expect(page.getByText(/返回 \d+ 行/)).toBeVisible()
  })

  test('rejected query shows error', async ({ page }) => {
    await page.goto('/')
    const textarea = page.getByLabel('SQL 输入')
    await textarea.fill('DELETE FROM customers')
    await page.getByRole('button', { name: '执行查询' }).click()
    await expect(page.getByText(/策略拒绝/)).toBeVisible({ timeout: 15000 })
    await expect(page.getByText(/FORBIDDEN_STATEMENT/)).toBeVisible()
  })

  test('empty SQL disables submit button', async ({ page }) => {
    await page.goto('/')
    const button = page.getByRole('button', { name: '执行查询' })
    await expect(button).toBeDisabled()
  })
})
