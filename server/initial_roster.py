"""Organizer-requested opening roster. Credentials are generated on each host."""
import uuid

from .credentials import new_team_code

ROSTER_VERSION = "yanghai-18-v1"
# Registration order is the order supplied by the organizer. Keep letter case
# and supplementary Unicode characters exactly as supplied.
INITIAL_ROSTER = (
    ("吳若林", "暫無", "吳暫聯隊"),
    ("張睿宸", "吳星毅", "史詠棋"),
    ("黃柏賓", "連家堯", "仁哥老師家教團隊"),
    ("邱品宸", "詹詠荃", "邱詹聯隊"),
    ("朱翊豪", "吳冠傑", "朱吳聯隊"),
    ("鍾承恩", "白尚平", "鍾白聯隊"),
    ("陳彥甫", "田宇恩", "宇恩哥哥好帥"),
    ("丁逸弘", "何柏穎", "丁何聯隊"),
    ("施景堯", "李雨謙", "師大圍棋社"),
    ("李宜倫", "劉晉翰", "labteam"),
    ("林軒志", "柯睿穎", "alphago"),
    ("鄭又仁", "楊以安", "隊名"),
    ("葉愷育", "歐又霖", "歐氏家族"),
    ("林皓寬", "陳顥昀", "窮山惡水雙子星"),
    ("林晸洋", "王茂申", "呵呵"),
    ("林彥光", "廖振翔", "AlphaGo"),
    ("曾浩銘", "陳浡𠄘", "我回歸了這次我要奪回屬於我的一切"),
    ("林恩洋", "黃晨軒", "火星衝衝衝"),
)


def build_initial_teams():
    teams = []
    for order, (captain, member, name) in enumerate(INITIAL_ROSTER, 1):
        teams.append({"id": uuid.uuid4().hex[:12], "name": name,
                      "players": [captain, member], "mars_points": [0, 0],
                      "checked_in": False, "eligibility_checked": False, "order": order,
                      "play_code": new_team_code(t["play_code"] for t in teams)})
    return teams
