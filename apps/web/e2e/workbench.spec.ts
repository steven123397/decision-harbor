import { expect, test } from '@playwright/test'


test('queues an allowed query and shows its run identity', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Ready')).toBeVisible()
  await page.getByRole('button', { name: 'Run query' }).click()

  await expect(page.getByText('Queued')).toBeVisible()
  await expect(page.getByText(/[0-9a-f]{8}-[0-9a-f-]{27}/)).toBeVisible()
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
