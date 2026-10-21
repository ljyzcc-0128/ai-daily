"""AI 行业日报：多源 RSS 采集 -> LLM 整理 -> PushPlus 推送."""
import datetime
import os
import socket

import feedparser
import requests

# 避免某个 RSS 源无响应时卡死整个任务
socket.setdefaulttimeout(30)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

FEEDS = {
    "IT之家": "https://www.ithome.com/rss/",
    "Solidot": "https://www.solidot.org/index.rss",
    "少数派": "https://sspai.com/feed",
    "Hacker News": "https://hnrss.org/frontpage",
    "Ars Technica AI": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    # 36氪/机器之心 2026-10 实测被反爬拦截（返回 HTML 挑战页），如后续恢复可重新启用：
    # "36氪": "https://36kr.com/feed",
    # "机器之心": "https://www.jiqizhixin.com/rss",
    # 可自行增删
}


def fetch_entries(hours=24):
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    items = []
    for name, url in FEEDS.items():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            if not feed.entries:
                print(f"[warn] {name} 未解析到条目（可能被反爬或源已失效）")
            for e in feed.entries:
                # 过滤时间窗口，避免重复推送旧闻
                published = e.get("published_parsed")
                if published:
                    dt = datetime.datetime(*published[:6], tzinfo=datetime.timezone.utc)
                    if dt < since:
                        continue
                items.append({
                    "source": name,
                    "title": e.get("title", "").strip(),
                    "link": e.get("link", ""),
                    "summary": e.get("summary", "")[:500],
                })
        except Exception as ex:
            print(f"[warn] {name} 抓取失败: {ex}")  # 单源失败不影响整体
    return items


def summarize(items):
    api_key = os.environ["LLM_API_KEY"]
    # 用 or 兜底：GitHub Actions 中未配置的 secret 会注入空字符串而非未设置
    # 默认智谱免费模型 glm-4-flash-250414，换模型只需配 LLM_MODEL（如 glm-5）
    base_url = os.environ.get("LLM_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4"
    model = os.environ.get("LLM_MODEL") or "glm-4-flash-250414"
    raw = "\n".join(
        f"- [{i['source']}] {i['title']} | {i['summary']} | {i['link']}"
        for i in items
    )
    prompt = f"""你是 AI 行业日报编辑。以下是过去24小时的资讯条目：
{raw}
请输出 Markdown 格式日报，要求：
1. 开头 3 条「要点提醒」，每条一句话
2. 按主题分栏（如：大模型、产品、开源、行业动态），每栏列 3-5 条
3. 每条保留来源和链接
4. 不要编造，只基于给定条目归纳
"""
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def push(content):
    token = os.environ["PUSHPLUS_TOKEN"]
    resp = requests.post(
        "https://www.pushplus.plus/send",
        json={
            "token": token,
            "title": "AI 行业日报",
            "content": content,
            "template": "markdown",
        },
        timeout=30,
    )
    resp.raise_for_status()
    print("push ok:", resp.json())


if __name__ == "__main__":
    items = fetch_entries(hours=24)
    if not items:
        print("今日无新条目，跳过推送")
        raise SystemExit(0)
    print(f"采集到 {len(items)} 条")
    daily = summarize(items)
    push(daily)
