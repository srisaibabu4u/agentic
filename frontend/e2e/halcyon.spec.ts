// The Halcyon renewal driven entirely through the UI: maker workbench, checker return,
// maker uploads the ownership evidence, checker approves. The worker moves the case in between.
import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const CASE = '/cases/RA-2026-0412'
const sample = (f: string) => path.resolve(import.meta.dirname, '../../samples', f)

const PASSWORD = 'kyc-demo-123' // seeded dummy accounts

/** Sign out (if signed in) and sign in as this user through the login form. */
async function actAs(page: Page, username: string) {
  const signOut = page.getByRole('button', { name: 'Sign out' })
  if (await signOut.isVisible()) await signOut.click()
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(signOut).toBeVisible()
}
const status = (page: Page) => page.locator('.case-head')
/** Screenshot of the action panel, kept with the test output (test-results/, git-ignored). */
const snap = (page: Page, name: string) =>
  page.locator('.action-card').screenshot({ path: test.info().outputPath(`${name}.png`) })

test('sign-in is required, and a wrong password is refused', async ({ page }) => {
  await page.goto(CASE)
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Username').fill('priya.nair')
  await page.getByLabel('Password').fill('not-the-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toHaveText('Wrong username or password.')
  await actAs(page, 'priya.nair')
  await expect(page.getByRole('heading', { name: 'Halcyon Marine Logistics Pte Ltd' })).toBeVisible()
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
})

test('Halcyon from first maker review to approval, through the UI', async ({ page }) => {
  await page.goto(CASE)
  await actAs(page, 'aaron.koh')
  // live updates: with the stream connected the page stops polling, so every later step relies on it
  await expect(status(page).getByText('● Live')).toBeVisible()

  // someone else's turn: no form, just who it is waiting on
  await expect(page.locator('.notice', { hasText: 'Waiting on Priya Nair' })).toContainText('Only they can act')
  await expect(page.getByRole('heading', { name: /Your turn/ })).toHaveCount(0)

  // ---- maker: confirm the obscured shareholding, dispose of the screening hit, upload FY2025 FS
  await actAs(page, 'priya.nair')
  await expect(page.getByRole('heading', { name: 'Your turn: maker workbench' })).toBeVisible()
  await expect(page.getByLabel('pct')).toHaveValue('45')
  await page.getByPlaceholder(/Evidence, e.g./).fill('Confirmed against client-signed register dated 2026-02-10')
  await page.getByRole('button', { name: 'Use these differences as the rationale' }).click()
  await expect(page.getByPlaceholder(/Rationale/)).toHaveValue('DOB 1971-04-02 vs listed 1958-09-17; nationality IN vs listed MY.')
  await page.getByRole('button', { name: 'Add', exact: true }).click()
  await page.getByLabel('Type / title').fill('Audited FS FY2025')
  await page.getByLabel('Document date').fill('2026-04-28')
  await page.getByLabel('Financial year end').fill('2025-12-31')
  await page.getByLabel('Text file').setInputFiles(sample('DOC-08_audited_fs_fy2025.txt'))
  await expect(page.getByText('Will send 1 upload, 1 confirmation, 1 disposition.')).toBeVisible()
  await snap(page, 'maker-workbench')
  await page.getByRole('button', { name: 'Submit to the master' }).click()
  await expect(page.getByText('Submitted. The agents are working on it')).toBeVisible()
  await expect(status(page).getByText('Waiting on Daniel Tan')).toBeVisible()

  // ---- checker: sees the maker's inputs, returns Q7
  await actAs(page, 'daniel.tan')
  await expect(page.getByRole('heading', { name: 'Your turn: checker review' })).toBeVisible()
  await expect(page.getByText(/false positive/i).first()).toBeVisible()
  const q7 = page.locator('.review-answers li', { hasText: 'Source of wealth of UBOs' })
  await q7.getByLabel('Return').check()
  await q7.getByRole('textbox').fill('Seacrest (BVI) holds 45%. Identify its ultimate owners and their source of wealth.')
  await snap(page, 'checker-return')
  await page.getByRole('button', { name: 'Return 1 question' }).click()
  await expect(status(page).getByText('Waiting on Priya Nair')).toBeVisible()

  // ---- maker: the suggested redraft is shown; upload the register of members and the SoW declaration
  await actAs(page, 'priya.nair')
  await expect(page.getByText('Q7 returned')).toBeVisible()
  await expect(page.getByText('Suggested redraft:')).toBeVisible()
  for (const [item, title, file] of [
    ['ubo_corporate_register', 'Seacrest register of members', 'DOC-09_seacrest_register.txt'],
    ['sow_declaration', 'SoW declaration + sale deed', 'DOC-10_sow_declaration.txt'],
  ]) {
    await page.getByRole('button', { name: '+ Add a document' }).click()
    const row = page.locator('.wb-item.upload').last()
    await row.locator('select').selectOption(item)
    await row.getByLabel('Type / title').fill(title)
    await row.getByLabel('Document date').fill('2026-11-04')
    await row.getByLabel('Text file').setInputFiles(sample(file))
  }
  await snap(page, 'maker-returned')
  await page.getByRole('button', { name: 'Submit to the master' }).click()
  await expect(status(page).getByText('Waiting on Daniel Tan')).toBeVisible()

  // ---- checker approves (two-step confirm)
  await actAs(page, 'daniel.tan')
  await expect(page.getByRole('heading', { name: 'Your turn: checker review' })).toBeVisible()
  await expect(page.locator('.review-answers li', { hasText: 'Source of wealth of UBOs' })).toContainText('Tan Hui Min')
  await page.getByRole('button', { name: 'Approve…' }).click()
  await page.getByRole('button', { name: 'Confirm: approve as MEDIUM' }).click()
  await expect(status(page).getByText('Closed')).toBeVisible()
  await expect(page.locator('.summary')).toContainText('Approved by Daniel Tan')
  await expect(page.locator('.timeline')).toContainText('R9')
})

test('replay the closed Halcyon case step by step', async ({ page }) => {
  await page.goto(CASE)
  await actAs(page, 'grace.lim')
  const current = page.locator('.flow-node.current')
  await expect(current).toContainText('Closed')

  await page.getByRole('button', { name: '⟲ Replay' }).click()
  await expect(current).toContainText('Discovery') // after step 1: NEW -> DISCOVERY (R1)
  await expect(page.locator('.replay-state')).toContainText('What the master saw')
  await page.getByRole('button', { name: 'Next step' }).click()
  await expect(current).toContainText('Ingestion') // after step 2: R2
  await expect(page.locator('.tl-item.future').first()).toBeVisible()

  await page.getByLabel('Replay step').fill('6') // drag the slider
  await expect(page.locator('.replay-bar')).toContainText('Step 7 of')
  await page.locator('.card', { has: page.locator('.replay') }).screenshot({ path: test.info().outputPath('replay-step-7.png') })

  await page.getByRole('button', { name: 'First step' }).click()
  await page.getByRole('button', { name: 'Play', exact: true }).click()
  await expect(page.locator('.replay-bar strong', { hasText: 'Live' })).toBeVisible() // played to the end
  await expect(current).toContainText('Closed')
  await page.getByRole('button', { name: 'Close replay' }).click()
  await expect(page.locator('.replay')).toHaveCount(0)
})
