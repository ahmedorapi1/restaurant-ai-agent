const API_URL = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const SESSION_KEY = 'sofra.session:' + API_URL

export type Message = { id: string; role: 'customer' | 'assistant'; text: string; images: string[] }
export type Confirmation = {
  id: string
  editable?: boolean
  stage?: 'proceed' | 'final'
  actions: { kind: 'create' | 'cancel'; details: string[] }[]
}
export type Conversation = { thread_id: string; messages: Message[]; pending_confirmation: Confirmation | null }
export type Session = { token: string; threadId: string }

export class ApiError extends Error {
  constructor(public readonly kind: 'network' | 'http' | 'invalid-response', public readonly status?: number) {
    super('Restaurant API request failed')
  }
}

export function isHttpError(error: unknown, status: number): boolean {
  return error instanceof ApiError && error.kind === 'http' && error.status === status
}

export function apiErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return 'حصل خطأ غير متوقع. حاول تاني.'
  if (error.kind === 'network') return 'تعذّر الوصول لسيرفر المطعم. اتأكد إن الخدمة شغالة وحاول تاني.'
  if (error.kind === 'invalid-response') return 'عنوان الخدمة رجّع رد غير متوقع. راجع إعدادات الاتصال.'
  switch (error.status) {
    case 401: return 'الجلسة انتهت. اضغط إعادة الاتصال.'
    case 404: return 'السيرفر مقدرش يلاقي المحادثة أو الخدمة المطلوبة (404).'
    case 409: return 'في إجراء لسه شغّال أو محتاج تأكيد. راجع المحادثة.'
    case 502: return 'السيرفر شغّال، لكن المساعد مقدرش يرد (502).'
    case 503: return 'السيرفر شغّال، لكن قاعدة البيانات مش متاحة (503).'
    default: return 'السيرفر رجّع خطأ (HTTP ' + error.status + '). حاول تاني.'
  }
}

async function request<T>(path: string, token?: string, method = 'GET', data?: unknown): Promise<T> {
  const headers = new Headers()
  if (token) headers.set('Authorization', 'Bearer ' + token)
  if (data !== undefined) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(API_URL + '/api' + path, {
      method, headers, body: data === undefined ? undefined : JSON.stringify(data), cache: 'no-store',
    })
  } catch {
    throw new ApiError('network')
  }
  if (!response.ok) throw new ApiError('http', response.status)
  try {
    return await response.json() as T
  } catch {
    throw new ApiError('invalid-response')
  }
}

const conversationPath = (threadId: string) => '/conversations/' + encodeURIComponent(threadId)

export const api = {
  startDemo: () => request<{ token: string }>('/customers/demo', undefined, 'POST'),
  startChat: (token: string) => request<Conversation>('/conversations', token, 'POST'),
  history: ({ token, threadId }: Session) => request<Conversation>(conversationPath(threadId) + '/messages', token),
  send: ({ token, threadId }: Session, message: string, confirmationId?: string) =>
    request<Conversation>(conversationPath(threadId) + '/messages', token, 'POST', { message, confirmation_id: confirmationId }),
  confirm: ({ token, threadId }: Session, id: string, decision: 'approve' | 'reject') =>
    request<Conversation>(conversationPath(threadId) + '/confirmation', token, 'POST', { confirmation_id: id, decision }),
}

export function menuImageUrl(reference: string): string | null {
  const localImage = API_URL + '/api/menu/image'
  if (reference === '/api/menu/image') return localImage
  try {
    const url = new URL(reference)
    const allowed = url.href === localImage || url.protocol === 'https:'
    return allowed && !url.username && !url.password ? url.href : null
  } catch {
    return null
  }
}

export function menuMessageText(text: string, imageUrl: string): string {
  // Strip only copies of the attached image, including copies saved in chat history.
  const cleaned = text.replace(
    /!\[[^\]\r\n]*\]\([ \t]*(?:<([^>\r\n]+)>|([^\s()]+(?:\([^()\r\n]*\)[^\s()]*)*))(?:[ \t]+(?:"[^"\r\n]*"|'[^'\r\n]*'|\([^()\r\n]*\)))?[ \t]*\)/g,
    (match, angled, bare) => menuImageUrl(angled ?? bare) === imageUrl ? '' : match,
  ).replace(/^[ \t]*(\S+)[ \t]*$/gm,
    (line, reference) => menuImageUrl(reference) === imageUrl ? '' : line,
  )
  if (cleaned === text && text.trim()) return text
  return cleaned.replace(/(?:\r?\n[ \t]*){2,}\r?\n/g, '\n\n').trim() || 'اتفضل، دي المنيو بتاعتنا 🍔\n\nتحب تطلب إيه؟'
}

export function readSession(): Session | null {
  try {
    const stored = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null')
    return stored && typeof stored.token === 'string' && typeof stored.threadId === 'string' ? stored : null
  } catch {
    return null
  }
}

export function saveSession(session: Session) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session))
  } catch {
    // Chat still works in this tab when browser storage is unavailable.
  }
}
