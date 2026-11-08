// 어떤 알림을 대기함에 넣을지 거르는 규칙.
//
// 아이폰 단축어는 카카오톡 알림 하나가 오면 증권사 자동화 3개(kis·meritz·nh)를 모두 실행할 수 있어서
// (제목 조건이 걸리지 않는 경우), 서버에서 한 번 더 거른다.
// 단축어가 보내는 글은 "보낸 곳(카톡 채널 이름)\n내용" 모양이라, 첫 줄로 보낸 곳을 확인한다.

// 회사별 보낸 곳 이름(첫 줄)에 들어가야 하는 말
const SENDERS: Record<string, RegExp> = {
  kis: /한국투자|한투|koreainvestment/i,
  meritz: /메리츠|meritz/i,
  nh: /NH투자|NH증권|나무증권|NH\s*나무|^NH/,
  upbit: /.*/, // 업비트는 앱이 정해져 있어 보낸 곳 확인을 하지 않는다
};

// 체결·배당 알림에 들어가는 말 (광고·공지·결제 알림은 빼려고)
const TRADE_WORDS = /체결|매수|매도|배당|분배금|이자\s*입금|주문/;

export function sender(text: string): string {
  return text.split("\n").map((s) => s.trim()).find(Boolean) ?? "";
}

// 받아야 하면 null, 버려야 하면 그 이유를 돌려준다.
export function rejectReason(source: string, title: string, body: string): string | null {
  const text = [title, body].filter(Boolean).join("\n");
  const rule = SENDERS[source];
  if (rule && source !== "upbit") {
    const from = title || sender(body);
    if (!rule.test(from)) return `보낸 곳이 ${source} 아님: ${from.slice(0, 20)}`;
  }
  if (!TRADE_WORDS.test(text)) return "체결·배당 알림이 아님";
  return null;
}
