// 카드 결제 알림 수신 함수 (지출관리 탭).
// 아이폰 단축어(iOS 27 '알림' 자동화)가 신한카드·현대카드 앱 푸시 내용을 POST로 보내면
// card_expenses 표에 원문 그대로 쌓는다. 일시·거래처·금액은 앱(lib/spending.py)이 읽어 채운다.
// 인증: 체결 알림(trade-inbox)과 같은 전송용 비밀번호(Edge Function 시크릿 INBOX_TOKEN).
//   헤더 `x-inbox-token: <비밀번호>` 또는 주소 끝 `?token=<비밀번호>` 또는 JSON의 "token".
// 카드: JSON "card"/"source" 또는 주소 끝 `&card=shinhan|hyundai` (없으면 내용으로 짐작).
// 본문: JSON {"title": "...", "subtitle": "...", "body": "..."} / 폼 / 그냥 글자 모두 받는다.
import { createClient } from "npm:@supabase/supabase-js@2";

const CARDS = ["shinhan", "hyundai"];

function detectCard(text: string): string {
  if (/신한|shinhan/i.test(text)) return "shinhan";
  if (/현대카드|hyundai/i.test(text)) return "hyundai";
  return "other";
}

function str(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

function reply(status: number, data: Record<string, unknown>): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return reply(405, { ok: false, error: "POST로 보내 주세요." });

  const url = new URL(req.url);
  const ctype = req.headers.get("content-type") ?? "";
  let data: Record<string, unknown> = {};
  try {
    if (ctype.includes("application/json")) {
      data = await req.json();
    } else if (ctype.includes("form")) {
      data = Object.fromEntries((await req.formData()).entries());
    } else {
      const text = await req.text();
      try {
        data = JSON.parse(text);
      } catch {
        data = { body: text };
      }
    }
  } catch {
    return reply(400, { ok: false, error: "내용을 읽지 못했어요." });
  }

  const expected = Deno.env.get("INBOX_TOKEN") ?? "";
  const given = req.headers.get("x-inbox-token") ?? url.searchParams.get("token") ?? str(data.token);
  if (!expected || given !== expected) return reply(401, { ok: false, error: "비밀번호가 맞지 않아요." });

  const title = str(data.title).trim();
  const subtitle = str(data.subtitle).trim();
  const main = str(data.body ?? data.text ?? data.message ?? data.content).trim();
  const body = [subtitle, main].filter(Boolean).join("\n");
  if (!body && !title) return reply(400, { ok: false, error: "알림 내용이 비어 있어요." });
  const givenCard = str(data.card ?? data.source ?? url.searchParams.get("card") ?? url.searchParams.get("source"))
    .trim().toLowerCase();
  const card = CARDS.includes(givenCard) ? givenCard : detectCard(`${givenCard} ${title} ${body}`);
  const raw = { ...data };
  delete raw.token;

  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false },
  });

  // 같은 알림이 10분 안에 또 오면 한 번만 저장한다.
  const since = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  const { data: dup } = await db.from("card_expenses").select("id")
    .eq("card", card).eq("body", body || title).gte("received_at", since).limit(1);
  if (dup && dup.length) return reply(200, { ok: true, duplicate: true, id: dup[0].id });

  const { data: row, error } = await db.from("card_expenses")
    .insert({ card, title: title || null, body: body || title, raw })
    .select("id").single();
  if (error) return reply(500, { ok: false, error: error.message });
  return reply(200, { ok: true, id: row.id, card });
});
