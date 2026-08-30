import { expect, test } from '@playwright/test'


test('queues an allowed query and shows its run identity', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('Queued')).toBeVisible()
  await expect(page.getByText(/[0-9a-f]{8}-[0-9a-f-]{27}/)).toBeVisible()
})


test('polls a queued run and shows its result table', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('Query succeeded')).toBeVisible({ timeout: 20000 })
  await expect(page.getByRole('table')).toBeVisible()
  await expect(page.getByRole('row').nth(1)).toContainText('East')
  await expect(page.getByText(/rows$/)).toBeVisible()
  await expect(page.getByText(/ms$/)).toBeVisible()
  await expect(page).toHaveURL(/run=[0-9a-f]{8}-[0-9a-f-]{27}/)
})


test('restores the same run, its SQL and its result after a reload', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(page.getByText('Query succeeded')).toBeVisible({ timeout: 20000 })
  const runId = new URL(page.url()).searchParams.get('run')

  await page.reload()

  await expect(page.getByText('Query succeeded')).toBeVisible({ timeout: 20000 })
  await expect(page.getByRole('table')).toBeVisible()
  await expect(page.getByLabel('SQL query')).toHaveValue(/revenue/)
  expect(new URL(page.url()).searchParams.get('run')).toBe(runId)
})


test('explains a result that is no longer retained', async ({ page }) => {
  await page.route('**/api/v1/query-runs/*/result', (route) =>
    route.fulfill({
      status: 410,
      contentType: 'application/json',
      body: JSON.stringify({
        data: null,
        error: { code: 'result_expired', message: 'The query result is no longer retained.' },
      }),
    }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('Result no longer retained')).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('result_expired')).toBeVisible()
  await expect(page.getByText(/Run the query again/)).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)
})


test('explains a run that has no result to read', async ({ page }) => {
  await page.route('**/api/v1/query-runs/*/result', (route) =>
    route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        data: null,
        error: { code: 'result_unavailable', message: 'This query run has no result to read.' },
      }),
    }),
  )
  await page.goto('/')
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('No result to read')).toBeVisible({ timeout: 20000 })
  await expect(page.getByText('result_unavailable')).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)
})


test('separates policy rejection from accepted submissions', async ({ page }) => {
  await page.goto('/')
  const editor = page.getByLabel('SQL query')

  await editor.fill('DELETE FROM customers')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(page.getByText('Query rejected')).toBeVisible()
  await expect(page.getByText('sql_statement_not_allowed')).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)

  await editor.fill('SELECT region, count(*) FROM customers GROUP BY region')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(page.getByText('Queued')).toBeVisible()
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

  await expect(page.getByText('Unavailable')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run query' })).toBeDisabled()
  ready = true
  await page.getByRole('button', { name: 'Check readiness' }).click()
  await expect(page.getByText('Ready')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run query' })).toBeEnabled()
})
