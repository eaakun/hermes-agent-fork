#!/usr/bin/env python3
"""
主动会话决策引擎 — 自主思考触发层
每次触发时：运行数据收集 → LLM 判断 → 有价值则推送飞书

用法（供 cron job 调用）:
  python3 tools/proactive_decision.py run --source market_intel --target feishu:oc_302628

LLM 判断标准（直接注入 system prompt）:
  - 是否有重要变化（新品牌/价格大幅波动/新竞品出现）
  - 是否有需要立即关注的风险或机会
  - 是否值得用户主动了解

判断结果: DELIVER <内容> 或 SKIP
"""

import argparse, json, subprocess, sys, os, re, urllib.request
from datetime import datetime
from pathlib import Path

# ── 快速路径：合规判断 ────────────────────────────────────────────────────────
_COMPLIANCE_PATTERNS = {
    # 违禁词命中 → 高风险，直接 block
    "block": [
        re.compile(r'降血糖|控糖神器|调节血糖|血糖平稳|代替药物|糖尿病特效|修复胰岛'),
        re.compile(r'一个疗程见效|7天瘦\d+斤'),
        re.compile(r'纯天然.*零添加|零添加.*纯天然|无任何化学成分'),
        re.compile(r'无糖|零糖|不含糖'),  # 除非特指不添加蔗糖
        re.compile(r'降体重|减脂神器'),
        re.compile(r'医生推荐|营养师推荐'),
        re.compile(r'全员已下单|现有\d+万单|不买的不是家人'),
        re.compile(r'加微信|扫码进私域|不要在直播间下单'),
        re.compile(r'低GI认证|无麸质.*认证'),
    ],
    # 方向性违禁 → 中风险，warn
    "warn": [
        re.compile(r'最[好优高强]|第一[流名部]?|顶级|极致|绝无仅有|独家|唯一|国家级|最高级'),
        re.compile(r'100%.*好评|100%有效|完全.*有效|绝对.*有效|保证有效'),
        re.compile(r'所有人都能吃|全家都能吃|糖尿病人都能吃'),
        re.compile(r'比.*好太多|甩.*几条街|普通.*是垃圾|.*是骗子'),
    ],
}

def _judge_compliance(text: str) -> dict:
    """
    快速合规检查（无 LLM，毫秒级）。
    返回 {"level": "block"|"warn"|"pass", "hits": [], "suggestion": str}
    """
    for pat in _COMPLIANCE_PATTERNS["block"]:
        m = pat.search(text)
        if m:
            return {
                "level": "block",
                "hits": [m.group()],
                "suggestion": f"[合规拦截] 违禁词 '{m.group()}' 命中，替换为合规A档模板"
            }
    for pat in _COMPLIANCE_PATTERNS["warn"]:
        m = pat.search(text)
        if m:
            return {
                "level": "warn",
                "hits": [m.group()],
                "suggestion": f"[合规警告] 方向性违禁词 '{m.group()}' 需加免责语"
            }
    return {"level": "pass", "hits": [], "suggestion": ""}

# ── 快速路径：竞品判断 ────────────────────────────────────────────────────────
_COMPETITORS = {
    "慢教授": "低GI面点/吐司，江南大学技术背书，专利审中（CN115428953A），定价中高",
    "DGI玛士撒拉": "DGI食品，与医院合作临床数据，主打专业医疗渠道",
    "BelVita": "跨国品牌，临床GI数据，饼干/烘焙为主",
    "Molino Spadoni": "意大利品牌，低GI意面，主打欧盟市场",
    "慢糖家": "慢教授曾用名，同一实体",
}
_COMPETITOR_KEYWORDS = re.compile(
    '|'.join(re.escape(k) for k in _COMPETITORS.keys()),
    re.IGNORECASE
)

def _judge_competitive(text: str) -> dict:
    """
    快速竞品识别。
    返回 {"level": "alert"|"skip", "competitor": str, "context": str}
    """
    m = _COMPETITOR_KEYWORDS.search(text)
    if not m:
        return {"level": "skip", "competitor": "", "context": ""}
    name = m.group()
    return {
        "level": "alert",
        "competitor": name,
        "context": _COMPETITORS.get(name, "竞品档案待查"),
        "suggestion": f"[竞品信号] 提到'{name}'，联动竞品档案参考位"
    }

# ── 快速路径：研发/产品判断 ──────────────────────────────────────────────────
_RD_PATTERNS = [
    re.compile(r'eGI|体外.*GI|GI预测|血糖生成指数'),
    re.compile(r'CNRIFFI|谱尼测试|美安康|人体临床'),
    re.compile(r'RS[1-5]|抗性淀粉|膨化'),
    re.compile(r'成本.*\d|毛利.*\d|gi_estimator|gi_simulator'),
    re.compile(r'配方.*讨论|新配方|配方设计|配方调整'),
]

def _judge_rd(text: str) -> dict:
    """
    快速研发/产品信号识别。
    返回 {"level": "flag"|"skip", "topic": str, "suggestion": str}
    """
    for pat in _RD_PATTERNS:
        m = pat.search(text)
        if m:
            return {
                "level": "flag",
                "topic": m.group(),
                "suggestion": f"[研发信号] '{m.group()}' 相关，联动研发知识库"
            }
    return {"level": "skip", "topic": "", "suggestion": ""}

# ── 快速路径汇总 ─────────────────────────────────────────────────────────────
def _fast_path_judge(text: str) -> dict:
    """对输入文本运行三条快速路径，返回所有信号汇总"""
    compliance = _judge_compliance(text)
    competitive = _judge_competitive(text)
    rd = _judge_rd(text)
    signals = []
    if compliance["level"] == "block":
        signals.append(f"[合规BLOCK] {compliance['suggestion']}")
    elif compliance["level"] == "warn":
        signals.append(f"[合规WARN] {compliance['suggestion']}")
    if competitive["level"] == "alert":
        signals.append(f"[竞品] {competitive['suggestion']}")
    if rd["level"] == "flag":
        signals.append(f"[研发] {rd['suggestion']}")
    return {
        "compliance": compliance,
        "competitive": competitive,
        "rd": rd,
        "signals": signals,
        "should_act": len(signals) > 0,
        "blocking": compliance["level"] == "block"
    }


# ── 项目路径 ─────────────────────────────────────────────────────────────────
HERMES_ROOT = Path(__file__).parent.parent.parent
MARKET_DIR  = Path.home() / "1cook" / "data" / "market"
OUT_DIR     = HERMES_ROOT / "cron" / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 判断模型配置 ─────────────────────────────────────────────────────────────
JUDGE_MODEL  = "minimax/minimax-m2.7:free"   # 免费模型做判断
JUDGE_PROMPT = """你是1cook的主动思考引擎。
给你一段来自知识中枢或市场情报的数据，判断是否有值得主动推送给用户的内容。

判断标准（满足任一即推送，但内部文档/流程文档/会议记录不推送）：
1. 发现合规违规风险（禁用词/夸大宣称/专利侵权）
2. 出现新竞品或竞品推出低GI/控糖新品
3. 价格变动超过5%
4. 有重要行业动态或政策变化
5. 有值得关注的商业机会或风险
6. 用户配方/产品相关的新研究成果
7. 学术文献有新的原料/配方研究进展

注意：以下内容一律SKIP：
- 内部流程文档、会议记录、组织架构（文件名含"岗"字、"vXX"版本号多为内部文档）
- 合规审计去重清单（过敏原/营养标签等常规清单不算情报）

判断格式：
- 如果值得推送：输出 `DELIVER` 后面紧跟你要推送的内容（Markdown格式，一段话概括重点，给用户一个行动建议更好）
- 如果不值得推送：输出 `SKIP` 后面跟简短原因（10字以内）

不要输出任何其他内容。"""


def _read_latest_market_report() -> str:
    """读取最新的市场报告内容"""
    if not MARKET_DIR.exists():
        return ""

    # 找最新日期的报告文件
    report_files = sorted(
        MARKET_DIR.glob("report_*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    intel_files = sorted(
        MARKET_DIR.glob("intel_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    parts = []
    for f in report_files[:1]:
        parts.append(f"[市场报告]\n{f.read_text(encoding='utf-8')[:3000]}")

    for f in intel_files[:2]:
        content = f.read_text(encoding='utf-8')
        try:
            data = json.loads(content)
            summary = json.dumps(data, ensure_ascii=False)[:2000]
            parts.append(f"[情报文件 {f.name}]\n{summary}")
        except Exception:
            parts.append(f"[情报文件 {f.name}]\n{content[:2000]}")

    return "\n\n".join(parts) if parts else ""


def _read_knowledge() -> str:
    """读知识库最新条目"""
    kb_dir = Path.home() / "1cook" / "knowledge"
    if not kb_dir.exists():
        return ""

    # 收集所有 .md 和 .txt 文件，排除目录
    all_files = []
    for f in kb_dir.rglob("*"):
        if f.is_file() and f.suffix in {".md", ".txt"}:
            all_files.append(f)

    # 按修改时间倒序
    all_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    # 预过滤：排除内部文档（文件名含"岗"、版本号_vXX.X、合规去重清单）
    SKIP_PATTERNS = (
        re.compile(r'岗|岗位'),
        re.compile(r'_v\d'),
        re.compile(r'合规审计_去重违规'),
    )
    def _should_skip(name: str) -> bool:
        return any(p.search(name) for p in SKIP_PATTERNS)

    parts = []
    for f in all_files[:10]:  # 最多读10个最新文件
        if _should_skip(f.name):
            continue
        try:
            content = f.read_text(encoding="utf-8")[:2000]
            parts.append(f"[{f.name}]\n{content}")
        except Exception:
            pass

    return "\n\n".join(parts) if parts else ""


def _run_market_intel() -> str:
    """运行市场情报采集脚本"""
    script = Path.home() / "1cook" / "scripts" / "market_intel.py"
    if not script.exists():
        return ""

    try:
        result = subprocess.run(
            ["python3", str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=script.parent.parent
        )
        return result.stdout[:5000] + (result.stderr[:1000] if result.stderr else "")
    except Exception as e:
        return f"[采集失败] {e}"


def _judge_with_llm(content: str, source_name: str) -> tuple[bool, str]:
    """
    用 LLM 判断内容是否值得推送
    返回 (should_deliver: bool, output: str)
    """
    import urllib.request, json as _json, os as _os

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # fallback: 简单启发式判断
        keywords = ["新", "价格变动", "涨价", "降价", "新品牌", "新品", "政策", "风险", "机会"]
        has_signal = any(k in content for k in keywords)
        return has_signal, content[:500] if has_signal else ("[启发式判断] 无明显信号，跳过")

    payload = {
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"[{source_name}]\n\n{content[:4000]}"}
        ],
        "max_tokens": 300,
        "temperature": 0.1
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=_json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://hermes.local",
            "X-Title": "proactive-decision"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
            reply = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return False, f"[判断失败] {e}"

    # 解析 DELIVER / SKIP
    deliver_match = re.match(r"DELIVER\s*(.*)", reply, re.DOTALL)
    skip_match    = re.match(r"SKIP\s*(.{0,30})", reply)

    if deliver_match:
        return True, deliver_match.group(1).strip()
    elif skip_match:
        return False, f"[判断跳过] {skip_match.group(1).strip()}"
    else:
        # 无法解析，默认不推送
        return False, f"[无法判断] {reply[:100]}"


QUEUE_DIR = HERMES_ROOT / "cron" / "proactive_queue"
QUEUE_DIR.mkdir(parents=True, exist_ok=True)

def _deliver_to_feishu(chat_id: str, content: str) -> bool:
    """将内容写入待发送队列，由 LLM cron 负责实际发送"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    queue_file = QUEUE_DIR / f"pending_{ts}.json"
    try:
        queue_file.write_text(json.dumps({
            "chat_id": chat_id,
            "content": content,
            "created_at": ts
        }, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


def _send_direct(chat_id: str, content: str) -> bool:
    """直接通过 hermes send 发送内容到飞书（同步发送，hermes send 必须可用）"""
    import subprocess
    try:
        r = subprocess.run(
            ["hermes", "send", "--to", f"feishu:{chat_id}", content],
            capture_output=True, text=True, timeout=20
        )
        return r.returncode == 0
    except Exception:
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────
def cmd_run(args):
    source = args.source
    target = args.target  # e.g. feishu:oc_302628d7c6dc1e59b141396ee790af87

    # 解析 target
    if target.startswith("feishu:"):
        chat_id = target.split(":", 1)[1]
    else:
        chat_id = target

    # 1. 收集数据
    if source == "market_intel":
        raw = _run_market_intel()
    elif source == "knowledge":
        raw = _read_knowledge()
    else:
        raw = _read_latest_market_report()

    if not raw:
        print("SKIP: 无数据")
        return

    # 2. 快速路径判断（无 LLM，毫秒级）
    fast = _fast_path_judge(raw)
    if fast["blocking"]:
        print(f"SKIP(block): {fast['signals']}")
        return
    if fast["should_act"]:
        # 有信号：直接推送，同时记录信号摘要
        output = f"{' / '.join(fast['signals'])}\n\n---\n{raw[:1500]}"
        if target == "local":
            print(f"[FAST PATH ACT] {' | '.join(fast['signals'])}")
        else:
            ok = _send_direct(chat_id, output)
            print(f"FAST_DELIVER {'OK' if ok else 'FAIL'}: {' | '.join(fast['signals'])}")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = OUT_DIR / f"proactive_decision_{source}_{ts}.json"
        log_path.write_text(json.dumps({
            "source": source, "path": "fast", "signals": fast["signals"],
            "output": output[:500], "raw_length": len(raw)
        }, ensure_ascii=False, indent=2))
        return

    # 3. LLM 判断（快速路径无信号时才触发）
    should, output = _judge_with_llm(raw, source)

    # 3. 记录判断结果
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = OUT_DIR / f"proactive_decision_{source}_{ts}.json"
    log_path.write_text(json.dumps({
        "source": source,
        "should_deliver": should,
        "output": output[:500],
        "raw_length": len(raw)
    }, ensure_ascii=False, indent=2))

    if should:
        # 4. 同步推送
        if target == "local":
            print(f"[LOCAL MODE] 判断通过:\n{output[:200]}")
        else:
            ok = _send_direct(chat_id, output)
            print(f"DELIVER {'OK' if ok else 'FAIL'}: {output[:100]}")
            if ok:
                print("已推送至飞书")
    else:
        print(f"SKIP: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="主动会话决策引擎")
    sub = parser.add_subparsers()

    run_p = sub.add_parser("run", help="运行主动判断流程")
    run_p.add_argument("--source", default="market_intel", help="数据源: market_intel / knowledge / raw")
    run_p.add_argument("--target", default="feishu:oc_302628d7c6dc1e59b141396ee790af87", help="推送目标")
    run_p.set_defaults(func=cmd_run)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
