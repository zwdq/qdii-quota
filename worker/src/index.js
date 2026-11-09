// qdii-quota 准点触发器
// GitHub Actions 自带的 schedule 高峰期常延迟 4~5 小时甚至直接跳过，
// 这里用 Cloudflare Worker 的 Cron（准点）调用 workflow_dispatch 启动抓取。
//
// 需要的密钥（wrangler secret put GH_TOKEN）：
//   fine-grained PAT，仅授权 zwdq/qdii-quota，权限 Actions: Read and write

const REPO = "zwdq/qdii-quota";
const WORKFLOW = "daily.yml";
const REF = "main";

async function dispatch(env) {
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "qdii-quota-trigger",
      },
      body: JSON.stringify({ ref: REF }),
    });
    if (resp.status === 204) {
      console.log(`dispatch ok (attempt ${attempt})`);
      return;
    }
    const text = await resp.text();
    console.error(`dispatch failed (attempt ${attempt}): ${resp.status} ${text.slice(0, 300)}`);
    // 4xx 多为令牌/权限问题，重试无意义
    if (resp.status < 500) break;
    await new Promise((r) => setTimeout(r, 5000 * attempt));
  }
  throw new Error("workflow_dispatch 失败，见日志");
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  // 不提供 HTTP 触发入口，避免被外部随意调用
  async fetch() {
    return new Response("qdii-quota trigger: cron only", { status: 404 });
  },
};
