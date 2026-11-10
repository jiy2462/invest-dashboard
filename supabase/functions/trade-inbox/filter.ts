// 어떤 알림을 대기함에 넣을지 거르는 규칙.
//
// 아이폰 단축어는 카카오톡 알림 하나가 오면 증권사 자동화 3개(kis·meritz·nh)를 모두 실행할 수 있어서
// (제목 조건이 걸리지 않는 경우), 서버에서 한 번 더 거른다.
// 단축어가 보내는 글은 "보낸 곳(카톡 채널 이름)\n내용" 모양이다.
//
// 결과는 세 가지다.
//   "keep"   : 체결·배당 알림 → 대기함(pending)
//   "hold"   : 그 증권사 알림이지만 체결·배당 말을 못 찾음 → 저장은 하되 '무시함'으로 (되돌릴 수 있게)
//   "drop"   : 그 증권사 이름이 아예 없음(다른 카톡 채널) → 저장하지 않음

// 회사별 이름
const BROKERS: Record<string, RegExp> = {
  kis: /한국투자|한투|koreainvestment/i,
  meritz: /메리츠|meritz/i,
  nh: /NH투자|NH증권|나무증권|NH\s*나무|(^|\n)\s*NH/,
};

// 체결·배당 알림에 들어가는 말 (광고·공지·결제 알림을 가려내려고)
const TRADE_WORDS = /체결|매수|매도|매매|배당|분배금|입금|주문|\b(buy|sell|filled|dividend)\b/i;

export type Verdict = { action: "keep" | "hold" | "drop"; reason?: string };

export function judge(source: string, title: string, body: string): Verdict {
  const text = [title, body].filter(Boolean).join("\n");
  const broker = BROKERS[source];
  if (broker && !broker.test(text)) return { action: "drop", reason: `${source} 알림이 아님` };
  if (!TRADE_WORDS.test(text)) return { action: "hold", reason: "체결·배당 말을 못 찾음" };
  return { action: "keep" };
}
