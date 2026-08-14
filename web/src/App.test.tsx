import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from './App'

describe('App', () => {
  it('renders the workbench with SQL input and submit button', () => {
    render(<App />)
    expect(screen.getByTestId('sql-input')).toBeTruthy()
    expect(screen.getByTestId('submit')).toBeTruthy()
  })
})
