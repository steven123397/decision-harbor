import { expect, test } from '@playwright/test'


test('runs an allowed query and renders its audit-backed result', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  const output = page.locator('section.output')
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('Query succeeded')).toBeVisible()
  await expect(page.getByRole('table')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: /region/ })).toBeVisible()
  await expect(output.getByText(/rows$/)).toBeVisible()
  await expect(output.getByText(/[0-9a-f]{8}-[0-9a-f-]{27}/)).toBeVisible()
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)
})


test('separates policy rejection from database execution failure', async ({ page }) => {
  await page.goto('/')
  const editor = page.getByLabel('SQL query')
  const output = page.locator('section.output')

  await editor.fill('DELETE FROM customers')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(output.getByText('Query rejected')).toBeVisible()
  await expect(output.getByText('sql_statement_not_allowed')).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)

  await editor.fill('SELECT missing_column FROM customers')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(output.getByText('Execution failed')).toBeVisible()
  await expect(output.getByText('query_semantic_error')).toBeVisible()
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)
})


test('marks row-limited results as truncated', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('SQL query').fill('SELECT id FROM orders ORDER BY id')
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('Query succeeded')).toBeVisible()
  await expect(page.getByText('Result truncated')).toBeVisible()
  await expect(page.locator('section.output').getByText('500 rows')).toBeVisible()
})


test('rechecks readiness before enabling submission after recovery', async ({ page }) => {
  let ready = false
  await page.route('**/ready', (route) =>
    route.fulfill({
      status: ready ? 200 : 503,
      contentType: 'application/json',
      body: JSON.stringify(
        ready
          ? { data: { status: 'ready' }, error: null }
          : { data: null, error: { code: 'service_not_ready', message: 'The service is not ready.' } },
      ),
    }),
  )
  await page.goto('/')

  await expect(page.getByText('Unavailable', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run query' })).toBeDisabled()
  ready = true
  await page.getByRole('button', { name: 'Check readiness' }).click()
  await expect(page.getByText('Ready', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run query' })).toBeEnabled()
})


// 4 表笛卡尔积远超 5s 语句超时：运行可稳定停留在 running，为取消留出真实窗口。
const SLOW_SQL = 'SELECT count(*) FROM customers a, orders b, order_items c, products d'


test('observes queued and running states, cancels the run, and offers retry', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  const output = page.locator('section.output')

  await page.getByLabel('SQL query').fill(SLOW_SQL)
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(output.getByText('Queued')).toBeVisible()
  await expect(output.getByText('Running')).toBeVisible({ timeout: 15_000 })

  await page.getByRole('button', { name: 'Cancel run' }).click()
  await expect(output.getByText('Query cancelled')).toBeVisible({ timeout: 20_000 })
  await expect(output.getByRole('button', { name: 'Retry run' })).toBeVisible()

  const history = page.getByRole('region', { name: 'Run history' })
  await expect(history.getByText('Cancelled').first()).toBeVisible()
})


test('retries a failed run as a new linked run', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  const output = page.locator('section.output')

  await page.getByLabel('SQL query').fill('SELECT missing_column FROM customers')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(output.getByText('Execution failed')).toBeVisible()
  const sourceRunId = await output.locator('.audit-facts dd').first().textContent()

  await output.getByRole('button', { name: 'Retry run' }).click()
  // Retry of 只出现在新运行上：它是重试真正发生的标记。
  await expect(output.getByText('Retry of')).toBeVisible({ timeout: 15_000 })
  await expect(output.getByText('Execution failed')).toBeVisible()
  expect(await output.locator('.audit-facts dd').first().textContent()).not.toBe(sourceRunId)
  await expect(output.getByText(sourceRunId ?? '')).toBeVisible()
})


test('history opens runs to read state and result', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(page.getByText('Query succeeded')).toBeVisible()

  await page.reload()
  const history = page.getByRole('region', { name: 'Run history' })
  await expect(history.getByRole('button').first()).toBeVisible()

  await history.getByRole('button').filter({ hasText: 'Succeeded' }).first().click()
  await expect(page.getByText('Query succeeded')).toBeVisible()
  await expect(page.getByRole('table')).toBeVisible()

  await history.getByRole('button').filter({ hasText: 'Failed' }).first().click()
  await expect(page.locator('section.output').getByText('Execution failed')).toBeVisible()
})


test('distinguishes expired and unavailable result feedback', async ({ page }) => {
  let statusCode = 410
  let errorCode = 'result_expired'
  await page.route('**/api/v1/query-runs/*/result', (route) =>
    route.fulfill({
      status: statusCode,
      contentType: 'application/json',
      body: JSON.stringify({
        data: null,
        error: { code: errorCode, message: 'Result feedback stub.', query_run_id: 'stub' },
      }),
    }),
  )
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  const output = page.locator('section.output')

  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(output.getByText('Result expired')).toBeVisible()
  await expect(output.getByText('result_expired')).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)

  statusCode = 409
  errorCode = 'result_unavailable'
  await page.reload()
  const history = page.getByRole('region', { name: 'Run history' })
  await expect(history.getByRole('button').first()).toBeVisible()
  await history.getByRole('button').filter({ hasText: 'Succeeded' }).first().click()

  await expect(output.getByText('Result unavailable')).toBeVisible()
  await expect(output.getByText('result_unavailable')).toBeVisible()
})


test('keeps the workbench usable on a narrow viewport without overlap', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  const output = page.locator('section.output')

  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(output.getByText('Query succeeded')).toBeVisible()
  await expect(page.getByRole('table')).toBeVisible()
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)

  await page.getByLabel('SQL query').fill('SELECT missing_column FROM customers')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(output.getByText('Execution failed')).toBeVisible()
  await expect(output.getByRole('button', { name: 'Retry run' })).toBeVisible()
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)

  await expect(page.getByRole('region', { name: 'Run history' }).getByRole('button').first()).toBeVisible()
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)
})


test('completes submit, cancel, and retry with keyboard only', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  const output = page.locator('section.output')

  // 键盘提交：聚焦 Run query 并回车。
  await page.getByLabel('SQL query').fill(SLOW_SQL)
  await page.getByRole('button', { name: 'Run query' }).focus()
  await page.keyboard.press('Enter')
  await expect(output.getByText('Queued')).toBeVisible()
  await expect(output.getByText('Running')).toBeVisible({ timeout: 15_000 })

  // 键盘取消：聚焦 Cancel run 并按空格。
  await page.getByRole('button', { name: 'Cancel run' }).focus()
  await page.keyboard.press('Space')
  await expect(output.getByText('Query cancelled')).toBeVisible({ timeout: 20_000 })

  // 键盘重试：聚焦 Retry run 并回车，新运行进入队列或运行。
  await page.getByRole('button', { name: 'Retry run' }).focus()
  await page.keyboard.press('Enter')
  await expect(output.getByText('Queued').or(output.getByText('Running'))).toBeVisible({ timeout: 15_000 })
})


function horizontalOverflow(page: import('@playwright/test').Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
}
