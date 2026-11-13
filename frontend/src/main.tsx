import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { api, apiErrorMessage, isHttpError, menuImageUrl, menuMessageText, readSession, saveSession, type Conversation, type Message, type Session } from './api'
import { Icon } from './icons'
import './style.css'

const welcome: Message = {
  id: 'welcome',
  role: 'assistant',
  text: 'أهلًا بحضرتك يا فندم، اتفضل.',
  images: [],
}

const checkoutLabels = {
  proceed: { heading: 'نكمل الطلب؟', approve: 'كمّل الطلب' },
  final: { heading: 'راجع طلبك قبل التأكيد النهائي', approve: 'أكد الطلب' },
}

function MenuImage({ url }: { url: string | null }) {
  const [failed, setFailed] = useState(false)
  if (!url || failed) return <p className="muted">صورة المنيو مش متاحة حاليًا.</p>
  return <a className="menu-image" href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
    <img src={url} alt="صورة منيو المطعم" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
    <span><Icon name="expand" size={14} /> عرض بالحجم الكامل</span>
  </a>
}

function MessageBubble({ message }: { message: Message }) {
  const reference = message.images[0]
  const imageUrl = reference ? menuImageUrl(reference) : null
  const text = imageUrl ? menuMessageText(message.text, imageUrl) : message.text
  return <div className={`bubble ${message.role}${imageUrl ? ' has-menu' : ''}`} role="group"
    aria-label={message.role === 'assistant' ? 'مساعد المطعم' : 'حضرتك'}>
    {text && <p dir="auto">{text}</p>}
    {reference && <MenuImage key={reference} url={imageUrl} />}
  </div>
}

async function restoreChat(session: Session): Promise<Conversation> {
  if (session.threadId) {
    try {
      return await api.history(session)
    } catch (error) {
      if (!isHttpError(error, 404)) throw error
    }
  }
  return api.startChat(session.token)
}

function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [draft, setDraft] = useState('')
  const [outgoing, setOutgoing] = useState('')
  const [busy, setBusy] = useState<'loading' | 'sending' | 'new' | 'confirm' | null>('loading')
  const [error, setError] = useState('')
  const scrollRef = useRef<HTMLElement>(null)
  const pending = conversation?.pending_confirmation
  const checkout = pending?.stage ? checkoutLabels[pending.stage] : undefined
  const blocksTyping = !!pending && !pending.editable

  function showChat(token: string, chat: Conversation) {
    const active = { token, threadId: chat.thread_id }
    saveSession(active)
    setSession(active)
    setConversation(chat)
  }

  async function connect() {
    setBusy('loading')
    setError('')
    try {
      const stored = readSession()
      if (stored) {
        try {
          showChat(stored.token, await restoreChat(stored))
          return
        } catch (error) {
          // Only replace an identity when the server explicitly rejects its token.
          if (!isHttpError(error, 401)) throw error
        }
      }
      const { token } = await api.startDemo()
      saveSession({ token, threadId: '' })
      showChat(token, await api.startChat(token))
    } catch (error) {
      setError(apiErrorMessage(error))
    } finally {
      setBusy(null)
    }
  }

  useEffect(() => { void connect() }, [])
  useEffect(() => {
    const area = scrollRef.current
    if (area) area.scrollTop = area.scrollHeight
  }, [conversation, busy, outgoing])

  async function newChat() {
    if (!session || busy) return
    setBusy('new')
    setError('')
    try {
      showChat(session.token, await api.startChat(session.token))
      setDraft('')
    } catch (error) {
      setError(apiErrorMessage(error))
    } finally {
      setBusy(null)
    }
  }

  async function recover(active: Session) {
    try {
      setConversation(await api.history(active))
    } catch {
      // Leave the last visible history in place.
    }
  }

  async function send() {
    const message = draft.trim()
    if (!session || !message || busy || blocksTyping) return
    setError('')
    setDraft('')
    setOutgoing(message)
    setBusy('sending')
    try {
      setConversation(await api.send(session, message, pending?.stage === 'final' ? pending.id : undefined))
    } catch (error) {
      await recover(session)
      setError(apiErrorMessage(error))
    } finally {
      setOutgoing('')
      setBusy(null)
    }
  }

  async function confirm(decision: 'approve' | 'reject') {
    if (!session || !pending || busy) return
    setBusy('confirm')
    setError('')
    try {
      setConversation(await api.confirm(session, pending.id, decision))
    } catch (error) {
      await recover(session)
      setError(apiErrorMessage(error) + ' راجع حالة الطلب قبل إعادة المحاولة.')
    } finally {
      setBusy(null)
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void send()
  }

  function sendOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void send()
    }
  }

  return <div className="chat">
    <header className="chat-header">
      <div className="chat-inner header-inner">
        <div>
          <h1>سُفرة</h1>
          <p>مساعد المطعم</p>
        </div>
        <button className="subtle-button new-chat" disabled={!session || busy !== null} onClick={() => void newChat()}>
          <Icon name="plus" size={16} /> محادثة جديدة
        </button>
      </div>
    </header>

    <main ref={scrollRef} className="chat-scroll" aria-label="محادثة المطعم">
      <div className="chat-inner message-list" role="log" aria-label="الرسائل">
        <MessageBubble message={welcome} />
        {conversation?.messages.map(message => <MessageBubble key={message.id} message={message} />)}
        {outgoing && <MessageBubble message={{ id: 'outgoing', role: 'customer', text: outgoing, images: [] }} />}
        {pending && <div className="bubble assistant confirmation">
          <p className="font-semibold">{checkout?.heading ?? 'في إجراء محتاج موافقة حضرتك'}</p>
          {pending.actions.map((action, index) => <div className="mt-2" key={index}>
            <p className="font-medium">{action.kind === 'cancel' ? 'إلغاء الطلب' : checkout ? 'ملخص الطلب' : 'تسجيل الطلب'}</p>
            <ul className="list-inside list-disc">{action.details.map((detail, item) => <li dir="auto" key={item}>{detail}</li>)}</ul>
          </div>)}
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="send-button" disabled={busy !== null} onClick={() => void confirm('approve')}>{checkout?.approve ?? 'تأكيد'}</button>
            <button className="subtle-button" disabled={busy !== null} onClick={() => void confirm('reject')}>{checkout ? 'مش هكمّل' : 'رفض'}</button>
          </div>
        </div>}
        {(busy === 'sending' || busy === 'confirm') && <p className="typing" role="status">جاري تجهيز الرد…</p>}
      </div>
    </main>

    <div className="composer">
      <div className="chat-inner">
        {error && <div className="error-banner" role="alert">
          <p>{error}</p>
          <button className="retry-button" disabled={busy !== null} onClick={() => void connect()}>إعادة الاتصال</button>
        </div>}
        <form className="composer-form" onSubmit={submit} aria-label="إرسال رسالة">
          <textarea rows={1} maxLength={2000} dir="auto" aria-label="رسالتك" placeholder="اكتب رسالتك…"
            value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={sendOnEnter}
            disabled={!session || busy === 'loading' || busy === 'new' || blocksTyping} />
          <button className="send-button" disabled={!session || busy !== null || blocksTyping || !draft.trim()} aria-label="إرسال">
            <Icon name="send" size={17} /> إرسال
          </button>
        </form>
      </div>
    </div>
  </div>
}

createRoot(document.getElementById('root')!).render(<App />)
