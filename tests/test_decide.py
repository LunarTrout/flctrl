"""Проверки перевода слышимости в правки.

Что обязано выполняться, иначе советы будут случайными числами:
1. равенство A = target даёт ноль дБ;
2. чем ниже слышимость, тем больше требуется;
3. кап держит одну правку в разумных пределах и помечается;
4. неактивные секции и роли с малой долей не трогаются;
5. правки отсортированы по недостатку слышимости.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import decide  # noqa: E402


def main() -> int:
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    check("на цели — ноль дБ", abs(decide.need_db(0.6, 0.6)) < 1e-6,
          "%.3f" % decide.need_db(0.6, 0.6))
    check("ниже цели — плюс", decide.need_db(0.3, 0.6) > 0,
          "%.2f дБ" % decide.need_db(0.3, 0.6))
    check("выше цели — минус", decide.need_db(0.9, 0.6) < 0,
          "%.2f дБ" % decide.need_db(0.9, 0.6))
    check("монотонность", decide.need_db(0.1, 0.6) > decide.need_db(0.5, 0.6),
          "%.2f против %.2f" % (decide.need_db(0.1, 0.6), decide.need_db(0.5, 0.6)))
    # A=0.3 при цели 0.6: шансы 3.5 → 10*lg(3.5) = 5.44 дБ
    check("формула совпадает с ручным счётом",
          abs(decide.need_db(0.3, 0.6) - 10 * math.log10(3.5)) < 1e-6,
          "%.3f" % decide.need_db(0.3, 0.6))

    attr = {
        "stems": {
            "keys": {"share_pct": 12.0, "sections": [
                {"t": 0, "active": True, "share": {"mid": 0.5, "pres": 0.3},
                 "regions": {"mid": 0.55, "pres": 0.30}},
                {"t": 8, "active": True, "share": {"mid": 0.5, "pres": 0.1},
                 "regions": {"mid": 0.70, "pres": 0.65}},
                {"t": 16, "active": False, "share": {"mid": 0.9, "pres": 0.1},
                 "regions": {"mid": 0.05, "pres": 0.05}},
            ]},
            "bass": {"share_pct": 15.0, "sections": [
                # бас в «воздухе» не звучит: чинить там нечего
                {"t": 0, "active": True, "share": {"air": 0.001, "bass": 0.7},
                 "regions": {"air": 0.00, "bass": 0.20}, "snr": {"air": -60.0, "bass": -9.0}},
            ]},
            "hiss": {"share_pct": 0.5, "sections": [
                {"t": 0, "active": True, "share": {"air": 0.9},
                 "regions": {"air": 0.10}},
            ]},
            "pad": {"share_pct": 8.0, "sections": [
                {"t": 0, "active": True, "share": {"mid": 0.6},
                 "regions": {"mid": 0.10}, "snr": {"mid": -8.0}},
                {"t": 8, "active": True, "share": {"mid": 0.6},
                 "regions": {"mid": 0.12}, "snr": {"mid": -6.0}},
            ]},
            "flat": {"share_pct": 20.0},
        }
    }
    res = decide.plan(attr, target=0.6, cap_db=3.0)
    edits = res["edits"]

    keys_pres = [e for e in edits if e["stem"] == "keys" and e["region"] == "pres"]
    check("проблемная полоса найдена", len(keys_pres) == 1, str(len(keys_pres)))
    check("кап применён и помечен", keys_pres and keys_pres[0]["capped"]
          and abs(keys_pres[0]["do_db"]) <= 3.0,
          str(keys_pres[0]["do_db"]) if keys_pres else "")
    check("дубли по секциям склеены",
          keys_pres and keys_pres[0]["n_sections"] == 1, str(keys_pres[0]["sections"]) if keys_pres else "")
    check("лёгкий недостаток без капа",
          any(e["stem"] == "keys" and e["region"] == "mid" and not e["capped"]
              for e in edits),
          str([(e["region"], e["do_db"]) for e in edits if e["stem"] == "keys"]))
    check("неактивная секция не тронута",
          all(16 not in e["sections"] for e in edits),
          str([e["sections"] for e in edits]))
    check("роль с малой долей пропущена",
          all(e["stem"] != "hiss" for e in edits)
          and any(s["stem"] == "hiss" for s in res["skipped"]), "")
    check("роль без секций пропущена",
          any(s["stem"] == "flat" for s in res["skipped"]), "")
    check("полоса без энергии не правится",
          all(e["region"] != "air" or e["stem"] != "bass" for e in edits),
          str([(e["stem"], e["region"]) for e in edits if e["stem"] == "bass"]))
    check("сортировка по важности",
          [e["impact"] for e in edits] == sorted([e["impact"] for e in edits], reverse=True), "")
    check("план ограничен по длине",
          res["count"] <= 12 and res["count"] == len(edits), "правок %d" % res["count"])

    # контроль усиления: только подъёмы → предупреждение; подъём со срезом → нет
    only_up = decide.plan({
        "stems": {
            "a": {"share_pct": 20.0, "sections": [
                {"t": 0, "active": True, "share": {"mid": 0.95}, "regions": {"mid": 0.05},
                 "snr": {"mid": -6.0}}]},
            "b": {"share_pct": 60.0, "sections": [
                {"t": 0, "active": True, "share": {"mid": 0.9}, "regions": {"mid": 0.90},
                 "snr": {"mid": +6.0}}]},
        },
    }, cap_db=3.0, min_deficit_db=1.0, min_share_pct=0.1, max_edits=1,
        targets={"other": {"mid": 0.0}}, roles_map={"a": "other", "b": "other"})
    check("компенсация гасит разгон",
          only_up["gain_staging"]["net_db"] <= 2.0
          or only_up["gain_staging"]["warn"] is False,
          "%+.2f дБ, компенсаций %d" % (only_up["gain_staging"]["net_db"],
                                        len(only_up["compensations"])))
    check("срез добавлен главному в полосе",
          bool(only_up["compensations"])
          and only_up["compensations"][0]["kind"] == "compensation"
          and only_up["compensations"][0]["do_db"] < 0,
          str([(c["stem"], c["region"], c["do_db"]) for c in only_up["compensations"]]))

    balanced = decide.plan({
        "stems": {
            "a": {"share_pct": 20.0, "sections": [
                {"t": 0, "active": True, "share": {"mid": 0.8}, "regions": {"mid": 0.1},
                 "snr": {"mid": -6.0}}]},
            "b": {"share_pct": 20.0, "sections": [
                {"t": 0, "active": True, "share": {"mid": 0.8}, "regions": {"mid": 0.8},
                 "snr": {"mid": +6.0}}]},
        },
    }, cap_db=3.0, min_deficit_db=1.0, min_share_pct=0.1,
        targets={"other": {"mid": 0.0}}, roles_map={"a": "other", "b": "other"})
    check("подъём со срезом гасит друг друга",
          balanced["gain_staging"]["warn"] is False
          and balanced["gain_staging"]["cuts"] >= 1,
          "%+.2f дБ (подъёмов %d, срезов %d)" % (
              balanced["gain_staging"]["net_db"], balanced["gain_staging"]["boosts"],
              balanced["gain_staging"]["cuts"]))

    pad = [e for e in edits if e["stem"] == "pad"]
    check("путь по отношению к миксу используется",
          bool(pad) and pad[0]["basis"] == "snr", pad[0]["basis"] if pad else "нет")
    check("правка по дБ верна и обрезана капом",
          bool(pad) and pad[0]["need_db"] == 7.0 and pad[0]["do_db"] == 3.0,
          str((pad[0]["need_db"], pad[0]["do_db"])) if pad else "")
    check("секции склеены в один участок",
          bool(pad) and pad[0]["n_sections"] == 2 and pad[0]["span"] == [0, 8], "")

    # цели из корпуса: роль bass в середине должна быть на 6 дБ ниже остального
    res_t = decide.plan(attr, cap_db=10.0, min_deficit_db=0.5,
                        targets={"bass": {"bass": -4.0}}, roles_map={"bass": "bass"})
    bass_edit = [e for e in res_t["edits"] if e["stem"] == "bass" and e["region"] == "bass"]
    check("цель из корпуса применяется",
          bool(bass_edit) and bass_edit[0]["goal_db"] == -4.0
          and abs(bass_edit[0]["need_db"] - (-4.0 - bass_edit[0]["snr_db"])) < 0.05,
          str((bass_edit[0]["goal_db"], bass_edit[0]["snr_db"],
               bass_edit[0]["need_db"])) if bass_edit else "нет")
    check("роль определена", bool(bass_edit) and bass_edit[0]["role"] == "bass", "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
