import { useState } from 'react'
import { motion } from 'framer-motion'
import { Brain, Lock, Unlock, Eye, EyeOff } from 'lucide-react'
import { authApi } from '../api/client'
import { sessionLogger } from '../utils/sessionLogger'

interface Props {
  onUnlocked: (token: string) => void
}

export function LockScreen({ onUnlocked }: Props) {
  const [pin, setPin] = useState('')
  const [showPin, setShowPin] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleUnlock = async () => {
    if (!pin || loading) return
    setLoading(true)
    setError('')
    sessionLogger.log('auth', 'unlock_attempt')
    try {
      const resp = await authApi.unlock(pin)
      sessionLogger.log('auth', 'unlock_success')
      onUnlocked(resp.token)
    } catch {
      sessionLogger.log('auth', 'unlock_failed')
      setError('Incorrect PIN. Try again.')
      setPin('')
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleUnlock()
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center"
      style={{ background: 'var(--bg)' }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 16 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="flex flex-col items-center gap-8 w-full max-w-sm px-6"
      >
        {/* Logo */}
        <div className="flex flex-col items-center gap-3">
          <div
            className="flex h-16 w-16 items-center justify-center rounded-2xl"
            style={{ background: 'var(--accent)' }}
          >
            <Brain size={32} color="#fff" />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-bold" style={{ color: 'var(--text)' }}>Engram</h1>
            <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
              Enter your PIN to unlock your memory
            </p>
          </div>
        </div>

        {/* PIN input */}
        <div className="w-full space-y-3">
          <div
            className="flex items-center gap-3 rounded-2xl border px-4 py-3"
            style={{
              borderColor: error ? '#ef4444' : 'var(--border)',
              background: 'var(--surface)',
            }}
          >
            <Lock size={16} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
            <input
              type={showPin ? 'text' : 'password'}
              value={pin}
              onChange={e => { setPin(e.target.value); setError('') }}
              onKeyDown={handleKeyDown}
              placeholder="PIN"
              autoFocus
              className="flex-1 bg-transparent text-sm outline-none tracking-widest"
              style={{ color: 'var(--text)' }}
            />
            <button
              onClick={() => setShowPin(s => !s)}
              style={{ color: 'var(--text-muted)', flexShrink: 0 }}
            >
              {showPin ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>

          {error && (
            <motion.p
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              className="text-xs text-center"
              style={{ color: '#ef4444' }}
            >
              {error}
            </motion.p>
          )}

          <button
            onClick={handleUnlock}
            disabled={!pin || loading}
            className="flex w-full items-center justify-center gap-2 rounded-2xl py-3 text-sm font-semibold transition-all"
            style={{
              background: pin && !loading ? 'var(--accent)' : 'var(--surface-2)',
              color: pin && !loading ? '#fff' : 'var(--text-muted)',
              cursor: pin && !loading ? 'pointer' : 'not-allowed',
            }}
          >
            <Unlock size={15} />
            {loading ? 'Unlocking…' : 'Unlock'}
          </button>
        </div>

        <p className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
          First time? Enter any PIN to set it permanently.
          <br />Your data never leaves this machine.
        </p>
      </motion.div>
    </div>
  )
}
