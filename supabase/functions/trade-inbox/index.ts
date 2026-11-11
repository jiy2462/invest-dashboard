// 체결·배당 알림 수신 함수.
// 아이폰 단축어(iOS 27 '알림' 자동화)가 알림 내용을 POST로 보내면 trade_inbox 표에 원문 그대로 쌓는다.
// 인증: Supabase 로그인(JWT) 대신 전송용 비밀번호(Edge Function 시크릿 INBOX_TOKEN)를 확인한다.
//   헤더 `x-inbox-token: <비밀번호>` 또는 주소 끝 `?token=<비밀번호>` 또는 JSON의 "token".
// 회사: JSON "source" 또는 주소 끝 `&source=upbit|kis|meritz|nh` (없으면 내용으로 짐작).
// 본문: JSON {"title": "...", "subtitle": "...", "body": "..."} / 폼 / 그냥 글자 모두 받는다.
import { createClient } from "npm:@supabase/supabase-js@2";
import { judge } from "./filter.ts";

const SOURCES: [RegExp, string][] = [
  [/업비트|upbit/i, "upbit"],
  [/한국투자|한투|koreainvestment|kis\b/i, "kis"],
  [/메리츠|meritz/i, "meritz"],
  [/NH|나무증권|농협/, "nh"],
];

function detectSource(...texts: string[]): string {
  const t = texts.join(" ");
  for (const [re, name] of SOURCES) if (re.test(t)) return name;
  return "other";
}

// 아이폰 단축어가 한글을 자모 단위로 쪼갠 꼴(NFD, 예: "ㅊㅔㄱㅕㄹ")로 보낼 때가 있어서
// (2026-11-11 메리츠 SGOV·QQQM 알림), 받자마자 보통 한글(NFC)로 합친다. 안 그러면 '체결'·'메리츠'를 못 찾는다.
function str(v: unknown): string {
  if (v === null || v === undefined) return "";
  return (typeof v === "string" ? v : JSON.stringify(v)).normalize("NFC");
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
  let text = "";
  try {
    if (ctype.includes("application/json")) {
      data = await req.json();
    } else if (ctype.includes("form")) {
      data = Object.fromEntries((await req.formData()).entries());
    } else {
      text = await req.text();
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
  const app = str(data.app).trim();
  const given_source = str(data.source ?? url.searchParams.get("source")).trim().toLowerCase();
  const source = ["upbit", "kis", "meritz", "nh"].includes(given_source)
    ? given_source
    : detectSource(given_source, app, title, body);
  const raw = { ...data };
  delete raw.token;

  // 다른 카톡 채널(광고·결제 등)은 저장하지 않고, 증권사 알림인데 체결·배당 말을 못 찾으면
  // '무시함'으로 저장해 둔다(놓친 체결을 '처리한 알림'에서 되돌릴 수 있게).
  const verdict = judge(source, title, body);
  console.log(JSON.stringify({
    source, action: verdict.action, reason: verdict.reason ?? null,
    keys: Object.keys(data), length: (title + body).length, preview: (title + " | " + body).slice(0, 80),
  }));
  if (verdict.action === "drop") return reply(200, { ok: true, skipped: true, reason: verdict.reason });
  const held = verdict.action === "hold";
  if (held) raw.skip_reason = verdict.reason;

  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false },
  });

  // 같은 알림이 10분 안에 또 오면 한 번만 저장한다.
  const since = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  const { data: dup } = await db.from("trade_inbox").select("id")
    .eq("source", source).eq("body", body || title).gte("received_at", since).limit(1);
  if (dup && dup.length) return reply(200, { ok: true, duplicate: true, id: dup[0].id });

  const { data: row, error } = await db.from("trade_inbox")
    .insert({
      source, title: title || null, body: body || title, raw,
      ...(held ? { status: "ignored", handled_at: new Date().toISOString() } : {}),
    })
    .select("id").single();
  if (error) return reply(500, { ok: false, error: error.message });
  return reply(200, { ok: true, id: row.id, source, held });
});
