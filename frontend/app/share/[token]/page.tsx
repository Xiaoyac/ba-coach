import type { Metadata } from "next";
import SharedConversationView from "@/components/SharedConversationView";

// Render only the shell on the server; the public API checks revocation on
// every load. No snapshot content is placed in Next's static page cache.
export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "只读对话分享 · BA Coach",
  description: "查看分享者保存的对话快照及本轮详情。",
  robots: { index: false, follow: false, noarchive: true },
  referrer: "no-referrer",
};

export default async function SharedConversationPage({ params }: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <SharedConversationView token={token} />;
}
