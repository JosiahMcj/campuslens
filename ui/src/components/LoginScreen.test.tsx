// @vitest-environment jsdom

// The sign-in screen: its words (product name, "Sign in", the plain
// sub-line, no event badge), the field errors under each field, the
// show/hide password button, the busy button, a named sign-in failure, and
// an unexpected failure that never leaks raw text.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const loginMock = vi.hoisted(() => vi.fn())

vi.mock('../auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth')>()
  return { ...actual, login: loginMock }
})

import { LoginError } from '../auth'
import { LoginScreen, SIGN_IN_FALLBACK_ERROR } from './LoginScreen'

afterEach(() => {
  cleanup()
  loginMock.mockReset()
})

function fill(email: string, password: string) {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: email } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: password } })
}

function submitButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: /sign in|signing in/i }) as HTMLButtonElement
}

describe('LoginScreen', () => {
  it('names the product and asks plainly, with no event badge', () => {
    const { container } = render(<LoginScreen notice={null} onSignedIn={() => {}} />)
    expect(screen.getByText('Golden Eagle AI Cabinet')).toBeTruthy()
    expect(screen.getByRole('heading', { level: 2, name: 'Sign in' })).toBeTruthy()
    expect(screen.getByText('Use the email your administrator set up.')).toBeTruthy()
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/Welcome back|Hackathon|Gloo|Boulder|→/)
    expect(text).not.toMatch(/Golden Eagle Cabinet\b/)
    expect(submitButton().textContent).toBe('Sign in')
    expect((screen.getByLabelText('Email') as HTMLInputElement).placeholder).toBe(
      'e.g. you@university.edu',
    )
  })

  it('shows the notice above the form', () => {
    render(<LoginScreen notice="Your session ended. Sign in again." onSignedIn={() => {}} />)
    expect(screen.getByRole('status').textContent).toBe('Your session ended. Sign in again.')
  })

  it('puts each problem under its own field and does not call sign in', () => {
    render(<LoginScreen notice={null} onSignedIn={() => {}} />)
    fireEvent.click(submitButton())
    const email = screen.getByLabelText('Email')
    const password = screen.getByLabelText('Password')
    expect(email.getAttribute('aria-invalid')).toBe('true')
    expect(document.getElementById(email.getAttribute('aria-describedby') ?? '')?.textContent).toBe(
      'Enter your email.',
    )
    expect(
      document.getElementById(password.getAttribute('aria-describedby') ?? '')?.textContent,
    ).toBe('Enter your password.')
    fill('someone@', 'x')
    fireEvent.click(submitButton())
    expect(screen.getByText('That email looks incomplete.')).toBeTruthy()
    expect(screen.queryByText('Enter your password.')).toBeNull()
    expect(loginMock).not.toHaveBeenCalled()
  })

  it('shows and hides the password', () => {
    render(<LoginScreen notice={null} onSignedIn={() => {}} />)
    const password = screen.getByLabelText('Password') as HTMLInputElement
    expect(password.type).toBe('password')
    fireEvent.click(screen.getByRole('button', { name: 'Show password' }))
    expect(password.type).toBe('text')
    fireEvent.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(password.type).toBe('password')
  })

  it('is busy while signing in, then hands the session over', async () => {
    let resolve: (value: unknown) => void = () => {}
    loginMock.mockReturnValue(new Promise((done) => (resolve = done)))
    const onSignedIn = vi.fn()
    render(<LoginScreen notice={null} onSignedIn={onSignedIn} />)
    fill(' president@demo.test ', 'secret')
    fireEvent.click(submitButton())
    expect(loginMock).toHaveBeenCalledWith('president@demo.test', 'secret')
    expect(submitButton().disabled).toBe(true)
    expect(submitButton().textContent).toBe('Signing in…')
    const session = { user: { id: 1 } }
    resolve(session)
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(session))
  })

  it('shows a named failure under the button and lets the person try again', async () => {
    loginMock.mockRejectedValue(new LoginError('That email and password did not work.'))
    render(<LoginScreen notice={null} onSignedIn={() => {}} />)
    fill('president@demo.test', 'wrong')
    fireEvent.click(submitButton())
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe('That email and password did not work.')
    expect(submitButton().disabled).toBe(false)
    expect(submitButton().textContent).toBe('Sign in')
    expect(submitButton().getAttribute('aria-describedby')).toBe(alert.id)
  })

  it('moves focus to the email field after a failed sign-in, which names the error', async () => {
    loginMock.mockRejectedValue(new LoginError('That email and password did not work.'))
    render(<LoginScreen notice={null} onSignedIn={() => {}} />)
    fill('president@demo.test', 'wrong')
    fireEvent.click(submitButton())
    const alert = await screen.findByRole('alert')
    const email = screen.getByLabelText('Email')
    await waitFor(() => expect(document.activeElement).toBe(email))
    expect(email.getAttribute('aria-describedby')).toBe(alert.id)
  })

  it('moves focus to the first field with a problem', () => {
    render(<LoginScreen notice={null} onSignedIn={() => {}} />)
    fireEvent.click(submitButton())
    expect(document.activeElement).toBe(screen.getByLabelText('Email'))
    fill('president@demo.test', '')
    fireEvent.click(submitButton())
    expect(document.activeElement).toBe(screen.getByLabelText('Password'))
  })

  it('never shows raw error text for an unexpected failure', async () => {
    loginMock.mockRejectedValue(new TypeError('Failed to fetch'))
    render(<LoginScreen notice={null} onSignedIn={() => {}} />)
    fill('president@demo.test', 'secret')
    fireEvent.click(submitButton())
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toBe(SIGN_IN_FALLBACK_ERROR)
    expect(alert.textContent).not.toMatch(/Failed to fetch/)
  })
})
