// 어떤 알림을 대기함에 넣을지 거르는 규칙.
//
// 아이폰 단축어는 카카오톡 알림 하나가 오면 증권사 자동화 3개(kis·meritz·nh)를 모두 실행할 수 있어서
// (제목 조건이 걸리지 않는 경우), 서버에서 한 번 더 거른다.
// 단축어가 보내는 글은 "보낸 곳(카톡 채널 이름)\n내용" 모양이다.
//
// 결과는 세 가지다.
//   "keep"   : 체결·배당 알림 → 대기함(pending)
//   "hold"   : 그 증권사 알림이지만 체결·배당 말을 못 찾음 → 저장은 하되 '무시함'으로 (되돌릴 수 있게)
//   "drop"   : (지금은 쓰지 않음) 2026-11-11 메리츠 QQQM 체결 알림이 '회사 이름 없음'으로 버려진 뒤로,
//              아이폰 단축어의 조건문이 이미 회사별로 거르므로 서버는 버리지 않고 모두 남긴다.

// 회사별 이름
const BROKERS: Record<string, RegExp> = {
  kis: /한국투자|한투|koreainvestment/i,
  meritz: /메리츠|meritz/i,
  nh: /NH투자|NH증권|나무증권|NH\s*나무|(^|\n)\s*NH/,
};

// 체결·배당 알림에 들어가는 말 (광고·공지·결제 알림을 가려내려고)
const TRADE_WORDS = /체결|매수|매도|매매|배당|분배금|입금|주문|\b(buy|sell|filled|dividend)\b/i;

// NH투자증권 '입금안내'는 은행에서 계좌로 돈을 넣었다는 이체 알림이다(보낸 은행·출금가능금액만 있음).
// 거래가 아니니 '자동 거름'으로 둔다. 배당·분배금 말이 함께 있으면 배당 입금일 수 있으니 거르지 않는다.
const NH_DEPOSIT = /입금\s*안내/;
const DIVIDEND_WORDS = /배당|분배금/;

export type Verdict = { action: "keep" | "hold" | "drop"; reason?: string };

export function judge(source: string, title: string, body: string): Verdict {
  const text = [title, body].filter(Boolean).join("\n");
  const broker = BROKERS[source];
  if (source === "nh" && NH_DEPOSIT.test(text) && !DIVIDEND_WORDS.test(text)) {
    return { action: "hold", reason: "NH 입금안내(이체)" };
  }
  // 체결·배당 말이 있으면, 증권사 이름이 글에 없더라도 그 회사 자동화가 보낸 것이니 대기함에 올린다.
  if (TRADE_WORDS.test(text)) return { action: "keep" };
  if (broker && !broker.test(text)) return { action: "hold", reason: "증권사 이름·체결 말을 못 찾음" };
  return { action: "hold", reason: "체결·배당 말을 못 찾음" };
}
