import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

declare global {
  // React only honours `act` when this flag is set, and this suite drives
  // timers by hand instead of relying on a test runner that sets it for us.
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true

afterEach(cleanup)
