import { expect, test } from '@playwright/test'


test('runs an allowed query and renders its audit-backed result', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('Query succeeded')).toBeVisible()
  await expect(page.getByRole('table')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: /region/ })).toBeVisible()
  await expect(page.getByText(/rows$/)).toBeVisible()
  const runId = await page.getByText(/[0-9a-f]{8}-[0-9a-f-]{27}/).textContent()
  expect(runId).toBeTruthy()

  await page.reload()
  await expect(page.getByText('Query succeeded')).toBeVisible()
  await expect(page.getByRole('table')).toBeVisible()
  await expect(page.getByText(runId!)).toBeVisible()
})


test('separates policy rejection from database execution failure', async ({ page }) => {
  await page.goto('/')
  const editor = page.getByLabel('SQL query')

  await editor.fill('DELETE FROM customers')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(page.getByText('Query rejected')).toBeVisible()
  await expect(page.getByText('sql_statement_not_allowed')).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)

  await editor.fill('SELECT missing_column FROM customers')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(page.getByText('Execution failed')).toBeVisible()
  await expect(page.getByText('query_semantic_error')).toBeVisible()
})


test('marks row-limited results as truncated', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('SQL query').fill('SELECT id FROM orders ORDER BY id')
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('Query succeeded')).toBeVisible()
  await expect(page.getByText('Result truncated')).toBeVisible()
  await expect(page.getByText('500 rows')).toBeVisible()
})


test('cancels a queued run from the workbench', async ({ page }) => {
  const runId = '11111111-1111-4111-8111-111111111111'
  const queuedRun = {
    id: runId,
    status: 'queued',
    policy_decision: 'allowed',
    policy_version: 'policy-v1',
    referenced_objects: ['analytics.customers'],
    statement_timeout_ms: 5000,
    max_rows: 500,
    returned_row_count: null,
    result_truncated: null,
    error_code: null,
    error_summary: null,
    created_at: '2026-01-01T00:00:00Z',
    started_at: null,
    finished_at: null,
    duration_ms: null,
  }
  await page.route('**/api/v1/query-runs', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ data: { query_run: queuedRun }, error: null }) })
  })
  await page.route(`**/api/v1/query-runs/${runId}`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: { query_run: queuedRun }, error: null }) })
  })
  await page.route(`**/api/v1/query-runs/${runId}/cancel`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: { query_run: { ...queuedRun, status: 'cancelled', finished_at: '2026-01-01T00:00:01Z', duration_ms: 1000 } }, error: null }),
    })
  })

  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(page.getByText('Queued')).toBeVisible()
  await page.getByRole('button', { name: 'Cancel query' }).click()
  await expect(page.getByText('Query cancelled')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel query' })).toHaveCount(0)
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
