import type { ReferenceChunk } from "@/lib/conversations";

const scoreLabels: Record<string, string> = {
  token_overlap: "词项重合分数", lexical: "词法检索分数", bm25f: "BM25F 排序分数",
  rrf: "RRF 融合分数", cross_encoder: "重排模型分数",
};

export function knowledgeScoreLabel(chunk: ReferenceChunk): string {
  if (chunk.score_type === "model_selection") return "模型筛选 · 未计算数值分数";
  if (chunk.score == null || !Number.isFinite(chunk.score)) return "数值分数未记录";
  const typeLabel = chunk.score_type ? scoreLabels[chunk.score_type] : undefined;
  // Historical snapshots did not identify the score source. Zero may be a
  // catalog placeholder OR a real zero; do not invent which one it was.
  if (!typeLabel && chunk.score === 0) return "评分方式未记录，暂不展示数值";
  return `${typeLabel ?? "检索分数（类型未记录）"}：${chunk.score.toFixed(4)}`;
}
