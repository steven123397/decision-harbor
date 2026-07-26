import { expect, test } from '@playwright/test'

// 浏览器主流程，对应 docs/design/testing.md：
// 输入 SQL → 提交 → 执行中状态 → 结果表格或拒绝原因。

test('允许的查询：展示结果表格与行数', async ({ page }) => {
  await page.goto('/')
  await page
    .getByLabel('SQL')
    .fill('SELECT region, COUNT(*) AS n FROM customers GROUP BY region ORDER BY region')
  await page.getByRole('button', { name: '提交' }).click()

  const table = page.getByRole('table')
  await expect(table).toBeVisible({ timeout: 15_000 })
  await expect(table).toContainText('East')
  await expect(page.getByText('返回 5 行', { exact: false })).toBeVisible()
})

test('执行期间展示执行中状态', async ({ page }) => {
  await page.goto('/')
  // 策略不允许 pg_sleep；用 1e8 规模的交叉连接制造可观察的执行窗口
  await page
    .getByLabel('SQL')
    .fill(
      'SELECT COUNT(*) FROM customers a CROSS JOIN customers b CROSS JOIN customers c CROSS JOIN customers d',
    )
  await page.getByRole('button', { name: '提交' }).click()

  await expect(page.getByText('执行中')).toBeVisible()
  await expect(page.getByRole('table')).toBeVisible({ timeout: 15_000 })
})

test('被拒绝的查询：展示错误码与记录标识', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('SQL').fill('DROP TABLE customers')
  await page.getByRole('button', { name: '提交' }).click()

  await expect(page.getByText('查询被拒绝')).toBeVisible()
  await expect(page.getByText('POLICY_NON_QUERY_STATEMENT')).toBeVisible()
  await expect(page.getByText('记录标识：')).toBeVisible()
})
