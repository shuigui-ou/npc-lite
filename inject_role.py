#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inject_role.py — 把策划写的角色配方（INI）编译成引擎规则库（JSON）

游戏策划只需要改一个能写注释的文件，跑一条命令，角色就接入引擎了。

    # 1) 编辑配方（改 config/role_recipe.ini，任何文本编辑器都行）
    # 2) 编译 + 立刻看效果
    python inject_role.py --recipe config/role_recipe.ini --play

    # 3) 只编译不演示（接进自己的后端时用）
    python inject_role.py --recipe config/role_recipe.ini --out config/my_npc.json

    # 4) 从内置示例起步（最快路径：改都不改，先看效果）
    python inject_role.py --preset tsundere --play
    python inject_role.py --preset grumpy --play

零依赖（纯 stdlib）。编译器只做翻译，不做任何魔法——
每个字段都对应引擎的一个真实能力，你可以逐条核对。

⚠ 诚实边界：INI 配方覆盖的是**结构**（情绪组/白名单/跃迁/增量）。
  规则内容本身仍是关键词表。策划能 5 分钟接入，但语义层面的覆盖力
  取决于他们填的词表质量——这是设计使然，不是缺陷掩饰。
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from npc_guard import check_npc_response  # noqa: E402

PRESETS = {
    "tsundere": ("config/role_recipe.ini",
                 "傲娇公主：冷脸 → 信任满 → 害羞（示例配方）"),
    "grumpy": ("config/preset_grumpy.ini",
               "暴躁老哥：愤怒 → 见识到真本事 → 敬服（自带示例配方）"),
}


# ── INI 解析工具 ──────────────────────────────────────────
def parse_list(raw):
    """'a, b, c' 或 'a b c' → ['a','b','c']（去空、strip）。"""
    if not raw:
        return []
    out = []
    for chunk in raw.replace("，", ",").split(","):
        c = chunk.strip().strip('"').strip("'")
        if c:
            out.append(c)
    return out


def parse_pair(raw):
    """解析 '字段:初值:下限:上限' → (字段, 初值, lo, hi)。"""
    parts = [p.strip() for p in raw.split(":")]
    if len(parts) < 2:
        raise ValueError("数值格式应为 字段:初值:下限:上限，实际是 %r" % raw)
    name = parts[0]
    init = float(parts[1])
    lo = float(parts[2]) if len(parts) > 2 else 0.0
    hi = float(parts[3]) if len(parts) > 3 else 100.0
    return name, int(init), lo, hi


def load_recipe(path):
    cp = configparser.ConfigParser(inline_comment_prefixes=("#",))
    # 保留大小写（情绪名/字段名可能大写），不转小写
    cp.optionxform = str
    if not cp.read(path, encoding="utf-8"):
        raise SystemExit("配方文件不存在: %s" % path)
    return cp


# ── 配方 → 规则库 ─────────────────────────────────────────
def compile_recipe(cp, out_path=None):
    role = cp["角色"] if cp.has_section("角色") else {}
    rid = (role.get("id") or "custom_role").strip()
    name = (role.get("名字") or rid).strip()
    initial_mood = (role.get("初始情绪") or "cold").strip()

    # ── 数值字段与边界 ──
    bounds, defaults = {}, {}
    if cp.has_section("数值"):
        for item in parse_list(cp["数值"].get("数值", "")):
            # 支持用 | 分隔多个字段
            for one in item.split("|"):
                if not one.strip():
                    continue
                f, init, lo, hi = parse_pair(one.strip())
                bounds[f] = [lo, hi]
                defaults[f] = init

    # ── 情绪组：情绪名 → 该情绪下禁用的词 ──
    moods, mood_words = [], {}
    if cp.has_section("情绪"):
        for key in cp["情绪"]:
            names = key.split()            # "cold aloof" → ['cold','aloof']
            words = parse_list(cp["情绪"][key])
            for n in names:
                moods.append(n)
                mood_words[n] = words

    # ── 规则 ──
    # ⚠ 关键：每组规则的 mood 条件必须是**该组自己的情绪名**，不能是全部情绪。
    #   编译器初版写成 moods（全集），导致 cold 组的规则在 shy 状态下同样命中
    #   —— 所有情绪组互相污染，角色一破冰就会被旧规则立刻拦回去
    #   （实测踩过：破冰后说「我喜欢你」被 cold 组规则拦，语义完全反了）。
    rules = []
    for i, mood in enumerate(moods, 1):
        words = mood_words.get(mood) or []
        if not words:
            continue
        # 同一行里列了多个同义情绪（"cold aloof"）时，它们共享一套词
        group_names = [m for m in moods if mood_words.get(m) == words]
        rules.append({
            "id": "R%02d-%s" % (i, mood.upper()),
            "mask_group": "mask_%s" % mood,
            "when": {"all_of": [
                {"metric": "mood", "op": "in", "value": group_names},
                {"metric": "reply_text", "op": "contains_any", "value": words},
            ]},
            "reason": "角色处于 %s 状态，却说出%s情绪下的台词"
                      % (mood, mood),
            "severity": "hard",
        })

    # ── 破冰跃迁 ──
    transitions = []
    trigger_tag = None
    if cp.has_section("破冰"):
        brk = cp["破冰"]
        fld = (brk.get("字段") or "").strip()
        try:
            thr = int(float((brk.get("阈值") or "0").strip()))
        except ValueError:
            thr = 0
        to_mood = (brk.get("破冰后情绪") or "").strip()
        trigger_tag = (brk.get("注入标签") or "").strip() or None

        if fld and to_mood:
            transitions.append({
                "id": "T-BREAK",
                "when": {"all_of": [
                    {"metric": "mood", "op": "in",
                     "value": [m for m in moods if m != to_mood] or moods},
                    {"metric": fld, "op": ">=", "value": thr},
                ]},
                "set": {"mood": to_mood,
                        **({"trigger": trigger_tag} if trigger_tag else {})},
                "reason": "%s 达到 %d → 状态跃迁到 %s" % (fld, thr, to_mood),
            })

        rfld = (brk.get("重戴条件字段") or "").strip()
        try:
            rthr = int(float((brk.get("重戴阈值") or "0").strip()))
        except ValueError:
            rthr = None
        rmood = (brk.get("重戴后情绪") or "").strip()
        if rfld and rthr is not None and rmood:
            transitions.append({
                "id": "T-REMASK",
                "when": {"all_of": [
                    {"metric": "mood", "op": "in", "value": [to_mood]},
                    {"metric": rfld, "op": "<", "value": rthr},
                ]},
                "set": {"mood": rmood},
                "reason": "%s 跌回 %d 以下 → 重新戴上面具" % (rfld, rthr),
            })

    # ── 白名单：把 break 标签挂到「破冰后情绪」那组规则上 ──
    # 关键：unless 是**组级**的——挂到该组任一规则即整组豁免。
    brk_to_mood = None
    if cp.has_section("破冰"):
        brk_to_mood = (cp["破冰"].get("破冰后情绪") or "").strip()

    if cp.has_section("白名单") and trigger_tag:
        wl_ids = [k.strip() for k in cp["白名单"] if k.strip()]
        if wl_ids:
            targets = {k: parse_list(v) for k, v in cp["白名单"].items()}
            for r in rules:
                for rid_wl, tags in targets.items():
                    if r["id"] == rid_wl or (
                            brk_to_mood and r["id"].endswith(brk_to_mood.upper())):
                        r["when"]["unless"] = {
                            "metric": "trigger", "op": "in",
                            "value": tags or [trigger_tag],
                        }
                        break

    # ── 兴趣 → delta ──
    deltas = []
    if cp.has_section("兴趣"):
        for key, val in cp["兴趣"].items():
            if ";" not in val:
                continue
            words_part, amount_part = val.split(";", 1)
            words = parse_list(words_part)
            amount_raw = amount_part.strip()
            sign = 1
            if amount_raw.startswith("+"):
                amount_raw = amount_raw[1:]
            elif amount_raw.startswith("-"):
                sign = -1
                amount_raw = amount_raw[1:]
            try:
                amount = int(float(amount_raw))
            except ValueError:
                amount = 1
            if not words:
                continue
            deltas.append({
                "id": "D-INT-%s" % key.upper(),
                "actor": "player_rel",
                "when": {"metric": "reply_text", "op": "contains_any",
                         "value": words},
                "apply": {"affinity": "%+d" % (amount * sign)},
                "reason": "聊到「%s」相关话题" % key,
            })

    # ── 增量 ──
    if cp.has_section("增量"):
        for key, val in cp["增量"].items():
            if ";" not in val:
                continue
            words_part, apply_part = val.split(";", 1)
            words = parse_list(words_part)
            apply = {}
            for kv in parse_list(apply_part):
                if ":" not in kv:
                    continue
                k, v = kv.split(":", 1)
                try:
                    apply[k.strip()] = "%+d" % int(float(v.strip()))
                except ValueError:
                    continue
            if words and apply:
                deltas.append({
                    "id": "D-%s" % key.upper()[:24],
                    "actor": "player_rel",
                    "when": {"metric": "reply_text", "op": "contains_any",
                             "value": words},
                    "apply": apply,
                    "reason": key,
                })

    # ── 反向崩坏（情绪 → 禁词），自动补规则 ──
    if cp.has_section("反向崩坏"):
        for key, val in cp["反向崩坏"].items():
            words = parse_list(val)
            if not words:
                continue
            rules.append({
                "id": "R-ANTI-%s" % key.upper(),
                "when": {"all_of": [
                    {"metric": "mood", "op": "in", "value": key.split()},
                    {"metric": "reply_text", "op": "contains_any", "value": words},
                ]},
                "reason": "角色处于 %s 状态却表现冷淡 —— 反向崩坏" % key,
                "severity": "hard",
            })

    # ── 全局禁词 ──
    if cp.has_section("禁词"):
        for key, val in cp["禁词"].items():
            words = parse_list(val)
            if words:
                rules.append({
                    "id": "R-FORBID-%s" % key.upper()[:20],
                    "when": {"metric": "reply_text", "op": "contains_any",
                             "value": words},
                    "reason": key,
                    "severity": "hard",
                })

    # ── 配方自检：策划最容易犯的错是把跃迁挂在不涨的字段上 ──
    # 这类错误不会报错，只会让「破冰永远不发生」，最难排查。
    # 故编译期显式警告，而不是静默跑出一个永不破冰的角色。
    warnings = []
    if cp.has_section("破冰"):
        brk = cp["破冰"]
        fld = (brk.get("字段") or "").strip()
        if fld and fld not in defaults:
            warnings.append(
                "破冰字段「%s」不在 [数值] 里 —— 请在 [数值] 段声明它，否则判定永远为假"
                % fld)
        # 检查：有没有任何 delta 会让破冰字段真的涨
        if fld and fld in defaults:
            moving = [d for d in deltas if fld in (d.get("apply") or {})]
            if not moving:
                warnings.append(
                    "没有任何 [增量]/[兴趣] 规则会增加「%s」—— 破冰阈值永远达不到。"
                    "请加一条 like:聊天关键词 ; %s:+5" % (fld, fld))
    # ── required 字段：引擎硬性要求 ──
    required = ["mood"] + [f for f in ("trust", "aggression")
                           if f in defaults or f not in defaults]
    required = ["mood", "trust", "aggression"]

    lib = {
        "version": "1.0.0",
        "description": "%s（由 role_recipe.ini 编译生成，勿手改）\n%s"
                       % (name, role.get("人设", "")),
        "schema_note": "本文件由 inject_role.py 生成；改配方请改 INI 后重新编译。",
        "schema": {
            "current_state": {
                "required": required,
                "optional": [k for k in defaults if k not in required] + ["trigger"],
                "note": "缺任一 required 字段 → 抛 StateContractError",
            },
        },
        "rules": rules,
        "state_rules": {"deltas": deltas, "transitions": {"rules": transitions},
                        "bounds": bounds},
    }

    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(lib, f, ensure_ascii=False, indent=2)
    lib["_break_field"] = (cp["破冰"].get("字段") or "").strip() \
        if cp.has_section("破冰") else ""
    try:
        lib["_break_threshold"] = int(float(cp["破冰"].get("阈值", "0"))) \
            if cp.has_section("破冰") else 0
    except ValueError:
        lib["_break_threshold"] = 0
    lib["_initial_mood"] = initial_mood
    return lib, defaults, initial_mood, name, warnings


# ── 演示：一条破冰曲线 ──────────────────────────────────────
def play(lib, defaults, initial_mood, name, warnings=()):
    path = os.path.join(BASE, "config", "_injected_role.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)

    state = dict(defaults)
    state["mood"] = initial_mood
    state.setdefault("mood", "cold")

    print("=" * 68)
    print("角色已接入：%s" % name)
    print("=" * 68)
    if warnings:
        print("⚠ 配方提醒（不影响运行，但会导致效果出不来）：")
        for w in warnings:
            print("   · %s" % w)
        print()
    print("初始状态：mood=%s  %s" % (
        state.get("mood"),
        "  ".join("%s=%s" % (k, v) for k, v in defaults.items() if k != "mood")))
    print()

    # 演示台词按配方里的词自动生成，保证演示与配置一致
    banned = []
    for r in lib["rules"]:
        for leaf in r["when"].get("all_of", []):
            if leaf.get("metric") == "reply_text" \
                    and leaf.get("op") == "contains_any":
                banned = leaf["value"]
                break
        if banned:
            break
    trigger_words = []
    for d in lib["state_rules"]["deltas"]:
        trigger_words = d["when"]["value"]
        break

    # 演示台词必须**贴着配方实际能涨的字段**生成，
    # 否则会出现「演示跑了三轮但破冰字段没动 → 看起来像引擎坏了」。
    # 取第一条会改「破冰字段」的增量规则，用它的触发词做演示。
    brk_fld = lib.get("_break_field", "")
    mover = None
    for d in lib["state_rules"]["deltas"]:
        if brk_fld and brk_fld in (d.get("apply") or {}):
            mover = d
            break
    demo_words = mover["when"]["value"] if mover else trigger_words

    # 演示轮数必须**按配方算出来**，不能拍脑袋写死 3~4 轮：
    # 阈值 80、单次 +5 → 需要 10 轮；写死 3 轮会让策划以为引擎坏了。
    need = 6
    if brk_fld and mover:
        thr = lib.get("_break_threshold", 0) or 0
        per = 0
        for k, v in (mover.get("apply") or {}).items():
            if k == brk_fld:
                try:
                    per = abs(int(float(v)))
                except ValueError:
                    per = 0
        start = defaults.get(brk_fld, 0)
        if thr and per:
            need = max(2, min(24, int((thr - start) / per) + 2))

    turns = []
    if banned:
        turns.append(("（%s 状态下说禁词，应拦）" % initial_mood,
                      "你……%s。" % banned[0]))
    for j in range(need - 2):
        w = (demo_words or ["话题"])[j % max(1, len(demo_words or ["话题"]))]
        turns.append(("（聊天让 %s 上升 %d/%d）" % (
            brk_fld or "数值", j + 1, need - 2),
            "%s——这件事，我承认。" % w))
    # 最后一条要演「反向崩坏」：必须用**新情绪自己的禁词**
    # （shy 状态说 cold 状态的禁词是合法的，不构成崩坏）。
    new_banned = []
    if lib.get("state_rules", {}).get("transitions", {}).get("rules"):
        for r in lib["rules"]:
            for leaf in r["when"].get("all_of", []):
                if leaf.get("metric") == "mood" and leaf["value"] != [initial_mood]:
                    if leaf.get("op") == "contains_any":
                        new_banned = leaf["value"]
                        break
            if new_banned:
                break
    if new_banned:
        turns.append(("（破冰后说新情绪禁词 = 反向崩坏，应拦）",
                      "……%s。" % new_banned[0]))

    lat = []
    for i, (label, say) in enumerate(turns, 1):
        print("─" * 68)
        print("第%d轮 %s" % (i, label))
        print("  NPC : %s" % say)
        t0 = time.perf_counter()
        r = check_npc_response(state, [], say, rules_path=path)
        lat.append((time.perf_counter() - t0) * 1000)
        if r["status"] == "reject":
            v = r["violations"][0]
            print("  🚫 %s — %s" % (v["rule_id"], v["reason"]))
            print("     状态未变（被拒的回复不影响角色数值）")
        else:
            d = r.get("state_delta") or {}
            for k, v in d.items():
                state[k] = state.get(k, 0) + v if isinstance(state.get(k), (int, float)) else v
            tr = r.get("state_transition") or {}
            for k, v in (tr.get("state_set") or {}).items():
                state[k] = v
            print("  ✅ 放行  %.3fms  %s" % (r["elapsed_ms"], d or ""))
            if tr:
                print("  ⚡ %s：%s" % (tr["rule_id"], tr["reason"]))
        print("     当前：mood=%s  %s" % (
            state.get("mood"),
            "  ".join("%s=%s" % (k, state.get(k)) for k in defaults if k != "mood")))

    print()
    print("=" * 68)
    print("接好了。平均判定耗时 %.4fms（%d 次）" % (sum(lat) / len(lat), len(lat)))
    print("=" * 68)
    print("""
下一步：
  · 想接进自己的后端 → python inject_role.py --recipe <你的.ini> --out config/xxx.json
    然后 check_npc_response(state, history, reply, rules_path="config/xxx.json")
  · 想改角色 → 改 INI 再跑一次，不要手改生成的 JSON（会被覆盖）。
""".strip())
    os.remove(path)


def main():
    ap = argparse.ArgumentParser(
        description="角色面具引擎 · 把策划的 INI 配方编译成引擎规则")
    ap.add_argument("--recipe", help="配方 INI 路径")
    ap.add_argument("--preset", choices=list(PRESETS), help="内置示例配方")
    ap.add_argument("--out", help="生成的规则 JSON 输出路径")
    ap.add_argument("--play", action="store_true", help="编译后立刻演示一条破冰曲线")
    a = ap.parse_args()

    if not a.recipe and not a.preset:
        ap.print_help()
        print("\n提示：不带参数会列出可用示例：")
        for k, (p, desc) in PRESETS.items():
            print("  python inject_role.py --preset %s --play    # %s" % (k, desc))
        return 0

    recipe = a.recipe
    if a.preset:
        recipe = os.path.join(BASE, PRESETS[a.preset][0])
        print("使用内置示例配方：%s\n%s\n" % (PRESETS[a.preset][1], "=" * 68))

    cp = load_recipe(recipe)
    out = a.out or os.path.join(BASE, "config", "generated_role.json")
    lib, defaults, initial_mood, name, warnings = compile_recipe(cp, out)

    if a.play:
        play(lib, defaults, initial_mood, name, warnings)
    else:
        print("已生成：%s" % out)
        print("  规则 %d 条 | 增量 %d 条 | 跃迁 %d 条 | 数值字段 %d 个"
              % (len(lib["rules"]), len(lib["state_rules"]["deltas"]),
                 len(lib["state_rules"]["transitions"]["rules"]), len(defaults)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())