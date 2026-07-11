import "jsr:@supabase/functions-js/edge-runtime.d.ts";

// ---------------------------------------------------------------------------
// CORS（GitHub Pages上の静的ページからブラウザ経由で直接呼び出すため必須）
// ---------------------------------------------------------------------------
const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS_HEADERS },
  });
}

// ---------------------------------------------------------------------------
// 競合スクレイピング（ベストエフォート）
// Yahoo!ショッピング検索結果のHTML構造やbot対策は予告なく変わりうるため、
// 失敗しても例外を投げず competitor_scrape_ok:false で処理を継続する。
// ---------------------------------------------------------------------------
const YAHOO_SEARCH_URL = "https://shopping.yahoo.co.jp/search?p=";

interface CompetitorHit { title: string; price: string | null }

async function fetchCompetitors(query: string): Promise<{ ok: boolean; source: string; hits: CompetitorHit[] }> {
  const source = YAHOO_SEARCH_URL + encodeURIComponent(query);
  try {
    const res = await fetch(source, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
      },
    });
    if (!res.ok) return { ok: false, source, hits: [] };
    const html = await res.text();

    // 商品名っぽい alt/title 属性値と円表記の価格を粗く拾う（サイト構造依存・変化に弱い）
    const titles = [...html.matchAll(/alt="([^"~"]{6,80}?)"/g)]
      .map((m) => m[1])
      .filter((t) => !/^(Yahoo|ヤフー|検索|ロゴ)/.test(t));
    const prices = [...html.matchAll(/([0-9,]{3,9})\s*円/g)].map((m) => m[1]);

    const hits: CompetitorHit[] = [];
    const seen = new Set<string>();
    for (let i = 0; i < titles.length && hits.length < 5; i++) {
      const t = titles[i].trim();
      if (!t || seen.has(t)) continue;
      seen.add(t);
      hits.push({ title: t, price: prices[i] ?? null });
    }
    return { ok: hits.length > 0, source, hits };
  } catch (_err) {
    return { ok: false, source, hits: [] };
  }
}

// ---------------------------------------------------------------------------
// Claude 呼び出し（構造化出力を tool_choice で強制）
// ---------------------------------------------------------------------------
const ANTHROPIC_MODEL = Deno.env.get("ANTHROPIC_MODEL") || "claude-sonnet-5";

const SUGGESTION_TOOL = {
  name: "submit_yahoo_copy_suggestion",
  description: "Yahoo!ショッピング出品ページの改善コピーを提案する",
  input_schema: {
    type: "object",
    required: [
      "headline",
      "caption",
      "abstract",
      "explanation",
      "proofread_notes",
      "competitor_notes",
      "image_prompts",
    ],
    properties: {
      headline: { type: "string", description: "60文字以内。目を引く、訴求力のある見出し（Yahoo headline列用）" },
      caption: { type: "string", description: "商品名の補足キャプション（Yahoo caption列用）" },
      abstract: { type: "string", description: "250文字程度の要約（Yahoo abstract列用）" },
      explanation: { type: "string", description: "500文字以内。伝わりやすさを重視した本文説明（Yahoo explanation列用）" },
      proofread_notes: {
        type: "array",
        items: { type: "string" },
        description: "誤字脱字・伝わりにくい表現・改善余地の具体的な指摘（箇条書き）",
      },
      competitor_notes: {
        type: "string",
        description: "競合出品と比較した上での差別化ポイント・改善提案。競合情報が得られなかった場合はその旨と一般的な改善提案",
      },
      image_prompts: {
        type: "array",
        items: { type: "string" },
        minItems: 3,
        maxItems: 3,
        description: "新規商品画像を3枚生成するための指示文（構図・雰囲気・訴求ポイントを具体的に）",
      },
    },
  },
};

interface SuggestInput {
  sale_id?: string;
  title: string;
  catchphrase?: string;
  description?: string;
  spec?: string;
  price?: number | string;
  images?: string[];
  competitor_query?: string;
}

async function callClaude(input: SuggestInput, competitors: CompetitorHit[], competitorOk: boolean) {
  const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
  if (!apiKey) throw new Error("ANTHROPIC_API_KEY が設定されていません");

  const competitorBlock = competitorOk && competitors.length
    ? competitors.map((c, i) => `${i + 1}. ${c.title}${c.price ? `（${c.price}円）` : ""}`).join("\n")
    : "（競合情報は取得できませんでした。一般的な観点で改善提案してください）";

  const system =
    "あなたは日本酒・マッコリなど専門酒屋『美酒館』のYahoo!ショッピング出品コピーライターです。" +
    "丁寧だが親しみやすいトーンで、以下の3つの観点から既存コピーを改善してください。" +
    "(1) 見出し・訴求文の魅力度（パッと見て興味を引くか）" +
    "(2) 説明文の伝わりやすさ（専門用語に頼らず商品の魅力が具体的に伝わるか）" +
    "(3) 競合出品と比較した差別化（同じような商品と何が違うのか、価格以外の強みを言語化できているか）" +
    "画像は撮り下ろしができない前提で、画像生成AIツールにそのまま入力できる新規候補画像3枚分の指示文（プロンプト）も提案してください（画像そのものは生成しません、文章のみでよい）。";

  const user = `【商品名】${input.title}
【現在のキャッチコピー】${input.catchphrase || "（なし）"}
【現在の説明文】${input.description || "（なし）"}
【スペック】${input.spec || "（なし）"}
【価格】${input.price ?? "（不明）"}

【競合出品（Yahoo!ショッピング検索結果より）】
${competitorBlock}`;

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: ANTHROPIC_MODEL,
      max_tokens: 2048,
      system,
      messages: [{ role: "user", content: user }],
      tools: [SUGGESTION_TOOL],
      tool_choice: { type: "tool", name: SUGGESTION_TOOL.name },
    }),
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Anthropic API error ${res.status}: ${body.slice(0, 500)}`);
  }
  const data = await res.json();
  const toolUse = (data.content || []).find((c: { type: string }) => c.type === "tool_use");
  if (!toolUse) throw new Error("Claudeから構造化出力が得られませんでした");
  return toolUse.input as {
    headline: string;
    caption: string;
    abstract: string;
    explanation: string;
    proofread_notes: string[];
    competitor_notes: string;
    image_prompts: string[];
  };
}

// ---------------------------------------------------------------------------
// エントリポイント
// 画像は生成せず、Claudeが提案する image_prompts（3案のプロンプト文）を
// そのままフロントに返す。撮影/画像生成は使う側が別途行う想定。
// ---------------------------------------------------------------------------
Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS_HEADERS });
  }
  if (req.method !== "POST") {
    return json({ ok: false, error: "POST のみ対応しています" }, 405);
  }

  try {
    const input = (await req.json()) as SuggestInput;
    if (!input || !input.title) {
      return json({ ok: false, error: "title は必須です" }, 400);
    }

    const competitorQuery = input.competitor_query || input.title;
    const competitors = await fetchCompetitors(competitorQuery);

    const suggestion = await callClaude(input, competitors.hits, competitors.ok);

    return json({
      ok: true,
      sale_id: input.sale_id ?? null,
      generated_at: new Date().toISOString(),
      headline: suggestion.headline,
      caption: suggestion.caption,
      abstract: suggestion.abstract,
      explanation: suggestion.explanation,
      proofread_notes: suggestion.proofread_notes,
      competitor_notes: suggestion.competitor_notes,
      competitor_source: competitors.source,
      competitor_scrape_ok: competitors.ok,
      image_prompts: suggestion.image_prompts,
    });
  } catch (err) {
    return json({ ok: false, error: String(err) }, 500);
  }
});
