import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import { createServer } from 'vite'

const vite = await createServer({ server: { middlewareMode: true, watch: null }, appType: 'custom' })
after(() => vite.close())
const { menuImageUrl, menuMessageText } = await vite.ssrLoadModule('/src/api.ts')
const local = menuImageUrl('/api/menu/image')

test('removes relative and absolute duplicates from an Arabic menu reply', () => {
  const text = `اتفضل، دي المنيو بتاعتنا 🍔\n\n![المنيو](/api/menu/image)\n![صورة المنيو](${local})\n\nتحب تطلب إيه؟`
  assert.equal(menuMessageText(text, local), 'اتفضل، دي المنيو بتاعتنا 🍔\n\nتحب تطلب إيه؟')
  assert.equal(menuImageUrl(local), local)
})

test('preserves ordinary links and content while removing the attached HTTPS image', () => {
  const remote = 'https://menu.example.test/assets/menu(v2).png?version=1'
  const otherContent = `[تفاصيل المنيو](${remote})\n[سياسة الطلب](/policy)\nالسعر حسب الصنف.`
  const text = `![المنيو](<${remote}> "صورة المنيو")\n${otherContent}`
  assert.equal(menuMessageText(text, menuImageUrl(remote)), otherContent)
})

test('uses a short caption when a duplicate is the entire reply', () => {
  assert.equal(menuMessageText('![المنيو](/api/menu/image)', local), 'اتفضل، دي المنيو بتاعتنا 🍔\n\nتحب تطلب إيه؟')
  assert.equal(menuMessageText(`/api/menu/image\nتحب تطلب إيه؟`, local), 'تحب تطلب إيه؟')
})

test('leaves non-matching text unchanged', () => {
  const text = 'طلبك اتسجل.\n\n\n![صورة تانية](https://other.example.test/api/menu/image)'
  assert.equal(menuMessageText(text, local), text)
  assert.equal(menuImageUrl('http://other.example.test/api/menu/image'), null)
})
