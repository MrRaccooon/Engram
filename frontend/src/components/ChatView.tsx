import { useRef, useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Send, Bot, User, Trash2, Zap, ShieldCheck, ChevronDown } from 'lucide-react'
import { useStore, type ChatMessage } from '../store/useStore'
import { askApi, type AskPreviewResponse } from '../api/client'
import { SensitivityModal } from './SensitivityModal'
import { sessionLogger } from '../utils/sessionLogger'

let _msgCounter = 0
const newId = () => `msg-${++_msgCounter}-${Date.now()}`

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}
    >
      {/* Avatar */}
      <div
        className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full"
        style={{ background: isUser ? 'var(--accent)' : 'var(--surface-2)' }}
      >
        {isUser
          ? <User size={14} color="#fff" />
          : <Bot size={14} style={{ color: 'var(--accent)' }} />
        }
      </div>

      {/* Content */}
      <div className={`max-w-[75%] space-y-1 ${isUser ? 'items-end' : 'items-start'} flex flex-col`}>
        <div
          className="rounded-2xl px-4 py-3 text-sm leading-relaxed"
          style={{
            background: isUser
              ? 'var(--accent)'
              : 'var(--surface-2)',
            color: isUser ? '#fff' : 'var(--text)',
            borderRadius: isUser ? '1rem 1rem 0.25rem 1rem' : '1rem 1rem 1rem 0.25rem',
          }}
        >
          <p className="whitespace-pre-wrap">{msg.content}</p>
        </div>

        {/* Meta row */}
        {msg.role === 'assistant' && (msg.model_used || msg.blocked_count !== undefined) && (
          <div className="flex items-center gap-3 px-1 text-xs" style={{ color: 'var(--text-muted)' }}>
            {msg.model_used && msg.model_used !== 'none' && (
              <span className="flex items-center gap-1">
                <Zap size={10} />
                {msg.model_used}
              </span>
            )}
            {msg.passing_count !== undefined && (
              <span className="flex items-center gap-1">
                <ShieldCheck size={10} />
                {msg.passing_count} sources · {msg.blocked_count ?? 0} blocked
              </span>
            )}
            {msg.query_time_ms !== undefined && (
              <span>{msg.query_time_ms}ms</span>
            )}
          </div>
        )}
      </div>
    </motion.div>
  )
}

function TypingIndicator() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      className="flex gap-3"
    >
      <div
        className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full"
        style={{ background: 'var(--surface-2)' }}
      >
        <Bot size={14} style={{ color: 'var(--accent)' }} />
      </div>
      <div
        className="flex items-center gap-1.5 rounded-2xl px-4 py-3"
        style={{ background: 'var(--surface-2)', borderRadius: '1rem 1rem 1rem 0.25rem' }}
      >
        {[0, 1, 2].map(i => (
          <motion.div
            key={i}
            className="h-2 w-2 rounded-full"
            style={{ background: 'var(--text-muted)' }}
            animate={{ opacity: [0.3, 1, 0.3], scale: [0.8, 1, 0.8] }}
            transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
          />
        ))}
      </div>
    </motion.div>
  )
}

export function ChatView() {
  const {
    chatMessages, addChatMessage, clearChat,
    isChatLoading, setChatLoading,
    sensitivityPreview, setSensitivityPreview,
    pendingChatQuery, setPendingChatQuery,
  } = useStore()

  const [input, setInput] = useState('')
  const [deep, setDeep] = useState(false)
  const [confirmEnabled, setConfirmEnabled] = useState(true)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [chatMessages, isChatLoading])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const executeAsk = async (query: string) => {
    setSensitivityPreview(null)
    setChatLoading(true)
    sessionLogger.log('ask', 'send', { query, deep })

    try {
      const resp = await askApi.ask(query, 10, deep)
      addChatMessage({
        id: newId(),
        role: 'assistant',
        content: resp.answer,
        blocked_count: resp.blocked_count,
        passing_count: resp.passing_count,
        model_used: resp.model_used,
        query_time_ms: resp.query_time_ms,
      })
      sessionLogger.log('ask', 'response', { model: resp.model_used, passing: resp.passing_count, blocked: resp.blocked_count }, resp.query_time_ms)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Request failed'
      addChatMessage({
        id: newId(),
        role: 'assistant',
        content: `Error: ${msg}`,
      })
      sessionLogger.log('ask', 'error', { error: msg })
    } finally {
      setChatLoading(false)
      setPendingChatQuery('')
    }
  }

  const handleSubmit = async () => {
    const query = input.trim()
    if (!query || isChatLoading) return

    setInput('')
    addChatMessage({ id: newId(), role: 'user', content: query })

    if (confirmEnabled) {
      setChatLoading(true)
      try {
        const preview: AskPreviewResponse = await askApi.preview(query, 10)
        setPendingChatQuery(query)
        setSensitivityPreview(preview)
      } catch {
        // If preview fails, ask directly
        await executeAsk(query)
      } finally {
        setChatLoading(false)
      }
    } else {
      await executeAsk(query)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleConfirm = () => {
    sessionLogger.log('ask', 'privacy_confirmed')
    if (pendingChatQuery) executeAsk(pendingChatQuery)
  }

  const handleCancel = () => {
    sessionLogger.log('ask', 'privacy_cancelled')
    setSensitivityPreview(null)
    setPendingChatQuery('')
    setChatLoading(false)
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div
        className="flex items-center gap-3 border-b px-6 py-3 flex-shrink-0"
        style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}
      >
        <Bot size={16} style={{ color: 'var(--accent)' }} />
        <span className="text-sm font-medium" style={{ color: 'var(--text)' }}>Ask Engram</span>
        <div className="flex-1" />

        {/* Deep mode toggle */}
        <button
          onClick={() => { setDeep(d => { sessionLogger.log('ask', 'toggle_deep', { deep: !d }); return !d }) }}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
          style={{
            background: deep ? 'color-mix(in srgb, var(--accent) 15%, transparent)' : 'var(--surface-2)',
            color: deep ? 'var(--accent)' : 'var(--text-muted)',
          }}
          title="Deep mode uses a more capable model"
        >
          <Zap size={11} />
          {deep ? 'Deep' : 'Standard'}
        </button>

        {/* Confirm toggle */}
        <button
          onClick={() => { setConfirmEnabled(c => { sessionLogger.log('ask', 'toggle_review', { review: !c }); return !c }) }}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
          style={{
            background: confirmEnabled ? 'color-mix(in srgb, #22c55e 12%, transparent)' : 'var(--surface-2)',
            color: confirmEnabled ? '#22c55e' : 'var(--text-muted)',
          }}
          title="Preview what is sent before each API call"
        >
          <ShieldCheck size={11} />
          {confirmEnabled ? 'Review on' : 'Review off'}
        </button>

        {chatMessages.length > 0 && (
          <button
            onClick={() => { sessionLogger.log('ask', 'clear_chat'); clearChat() }}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors"
            style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}
            title="Clear conversation"
          >
            <Trash2 size={11} />
            Clear
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
        {chatMessages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <div
              className="flex h-16 w-16 items-center justify-center rounded-2xl"
              style={{ background: 'var(--surface-2)' }}
            >
              <Bot size={32} style={{ color: 'var(--accent)' }} />
            </div>
            <div>
              <p className="font-semibold text-lg" style={{ color: 'var(--text)' }}>
                Ask about your past
              </p>
              <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                "What was I working on last Tuesday?"<br />
                "When did I look at the auth module?"<br />
                "What articles did I read about React?"
              </p>
            </div>
            <div
              className="flex items-center gap-2 rounded-full px-4 py-2 text-xs"
              style={{
                background: 'color-mix(in srgb, #22c55e 10%, transparent)',
                color: '#22c55e',
              }}
            >
              <ShieldCheck size={12} />
              Sensitive data is filtered before leaving your machine
            </div>
          </div>
        )}

        <AnimatePresence initial={false}>
          {chatMessages.map(msg => (
            <MessageBubble key={msg.id} msg={msg} />
          ))}
        </AnimatePresence>

        <AnimatePresence>
          {isChatLoading && !sensitivityPreview && <TypingIndicator />}
        </AnimatePresence>

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div
        className="border-t px-6 py-4 flex-shrink-0"
        style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}
      >
        <div
          className="flex items-end gap-3 rounded-2xl border px-4 py-3"
          style={{ borderColor: 'var(--border)', background: 'var(--surface-2)' }}
        >
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your past… (Enter to send, Shift+Enter for newline)"
            rows={1}
            className="flex-1 resize-none bg-transparent text-sm outline-none"
            style={{
              color: 'var(--text)',
              maxHeight: '120px',
              overflowY: input.split('\n').length > 4 ? 'auto' : 'hidden',
            }}
            disabled={isChatLoading}
          />
          <button
            onClick={handleSubmit}
            disabled={!input.trim() || isChatLoading}
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl transition-all"
            style={{
              background: input.trim() && !isChatLoading ? 'var(--accent)' : 'var(--border)',
              color: '#fff',
              cursor: input.trim() && !isChatLoading ? 'pointer' : 'not-allowed',
            }}
          >
            {isChatLoading
              ? <ChevronDown size={14} />
              : <Send size={14} />
            }
          </button>
        </div>
        <p className="mt-2 text-center text-xs" style={{ color: 'var(--text-muted)' }}>
          Retrieval is 100% local · Only masked context reaches the AI
        </p>
      </div>

      {/* Sensitivity confirmation modal */}
      <SensitivityModal onConfirm={handleConfirm} onCancel={handleCancel} />
    </div>
  )
}
