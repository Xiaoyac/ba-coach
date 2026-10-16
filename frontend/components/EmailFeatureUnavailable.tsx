import Link from "next/link";

export default function EmailFeatureUnavailable() {
  return <main className="flex min-h-[100dvh] items-center justify-center bg-canvas p-6">
    <section className="w-full max-w-md rounded-2xl border border-line bg-panel p-7 text-ink">
      <h1 className="text-xl font-semibold">邮箱功能已停用</h1>
      <p className="mt-4 text-sm leading-7 text-ink-muted">此链接不再用于验证邮箱或重置密码。你仍可使用账号和密码登录；登录后可在设置中修改密码。</p>
      <p className="mt-3 text-sm leading-7 text-ink-muted">忘记密码时暂不支持自助找回，请联系管理员。</p>
      <Link href="/" className="mt-6 block rounded-xl border border-accent-edge bg-accent-wash p-3 text-center text-accent-ink">返回登录</Link>
    </section>
  </main>;
}
