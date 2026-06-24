async page => {
  const site = process.env.ZULIP_SITE;
  const botEmail = process.env.ZULIP_BOT_EMAIL;
  const loginEmail = process.env.ZULIP_MY_EMAIL;
  const loginPassword = process.env.ZULIP_MY_PAS;
  const runId = Math.random().toString(16).slice(2, 10);

  if (!site || !botEmail) {
    throw new Error("ZULIP_SITE and ZULIP_BOT_EMAIL are required");
  }

  await page.goto(site, { waitUntil: "domcontentloaded" });
  const emailBox = page.locator("input[name=email], input[type=email]").first();
  if (await emailBox.isVisible().catch(() => false)) {
    if (!loginEmail || !loginPassword) {
      throw new Error("ZULIP_MY_EMAIL and ZULIP_MY_PAS are required for login");
    }
    await emailBox.fill(loginEmail);
    await page.locator("input[name=password], input[type=password]").first().fill(loginPassword);
    await page.locator("button[type=submit], input[type=submit]").first().click();
    await page.waitForLoadState("domcontentloaded");
  }
  await page.waitForFunction(() => document.readyState !== "loading", null, { timeout: 30000 });

  const proof = await page.evaluate(async ({ botEmail, runId }) => {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const csrf = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] || "";

    async function api(path, options = {}) {
      const headers = new Headers(options.headers || {});
      if (options.method && options.method !== "GET") {
        headers.set("X-CSRFToken", decodeURIComponent(csrf));
      }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      let response;
      let text;
      try {
        response = await fetch(path, {
          credentials: "same-origin",
          ...options,
          headers,
          signal: controller.signal,
        });
        text = await response.text();
      } finally {
        clearTimeout(timer);
      }
      let body;
      try {
        body = text ? JSON.parse(text) : {};
      } catch {
        body = { raw: text };
      }
      if (!response.ok || body.result === "error") {
        throw new Error(`${options.method || "GET"} ${path} failed ${response.status}: ${text.slice(0, 500)}`);
      }
      return body;
    }

    const users = await api("/api/v1/users?client_gravatar=false");
    const bot = (users.members || []).find(user => user.email === botEmail);
    if (!bot) {
      throw new Error(`Bot user not found for ${botEmail}`);
    }

    const content = `direct e2e ping ${runId}. Reply with one short sentence.`;
    const form = new URLSearchParams();
    form.set("type", "direct");
    form.set("to", JSON.stringify([bot.user_id]));
    form.set("content", content);
    const sent = await api("/api/v1/messages", { method: "POST", body: form });
    const triggerId = sent.id;

    const narrow = JSON.stringify([{ operator: "dm", operand: bot.user_id }]);
    const query = new URLSearchParams({
      anchor: "newest",
      num_before: "50",
      num_after: "0",
      narrow,
      apply_markdown: "false",
    });
    const deadline = Date.now() + 120000;
    let last = { count: 0, sawTrigger: false, sawReaction: false, sawProgress: false, replies: 0 };

    while (Date.now() < deadline) {
      const data = await api(`/api/v1/messages?${query.toString()}`);
      const messages = data.messages || [];
      const trigger = messages.find(message => message.id === triggerId);
      const sawReaction = !!(trigger?.reactions || []).find(
        reaction =>
          reaction.emoji_name === "+1" ||
          reaction.emoji_name === "thumbs_up" ||
          reaction.emoji_code === "1f44d",
      );
      const progress = messages.find(
        message =>
          message.id > triggerId &&
          message.sender_email === botEmail &&
          String(message.content || "").includes("Working on it"),
      );
      const replies = messages.filter(
        message =>
          message.id > triggerId &&
          message.sender_email === botEmail &&
          !String(message.content || "").includes("Working on it"),
      );
      last = {
        count: messages.length,
        sawTrigger: !!trigger,
        sawReaction,
        sawProgress: !!progress,
        replies: replies.length,
      };
      if (trigger && sawReaction && replies.length > 0) {
        const reply = replies[replies.length - 1];
        return {
          runId,
          botUserId: bot.user_id,
          triggerId,
          replyId: reply.id,
          sawReaction,
          sawProgress: !!progress,
          replyPreview: String(reply.content || "").slice(0, 240),
        };
      }
      await sleep(2000);
    }

    throw new Error(`Timed out waiting for direct reply to ${triggerId}: ${JSON.stringify(last)}`);
  }, { botEmail, runId });

  await page.goto(`${site}/#narrow/dm/${proof.botUserId}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);
  return proof;
}
