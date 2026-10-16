"""Organizer-requested roster and deliberately pre-issued, printable codes."""
import uuid

ROSTER_VERSION = "yanghai-18-v2-static"
RULE_VERSION = "v0.5.0"
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

# The organizer explicitly chose fixed codes so printed cards work on every
# fresh event installation. These credentials are intentionally public.
INITIAL_CODES = (
    "J9Y2-L4DG-JF7L", "WBRN-K9WJ-BC7Y", "9LWY-RWFC-6ZLH",
    "BA29-VP7J-T7WE", "YFRG-ZZLA-VR8X", "EZ5E-4J5U-W96H",
    "SF5L-WHKH-U4EL", "DSHD-9645-RBM3", "HX3W-ZC4C-PP7J",
    "4GM3-MNA6-44MG", "Q7F4-PA79-B34E", "SKRD-XV77-QHMA",
    "JCJN-SYHR-8GD4", "YV84-4CE8-LQQX", "8ZEJ-4H4B-FMZJ",
    "AU3K-8ZZQ-XRZE", "WGBD-ZWQL-E34X", "D78K-64PG-S73S",
)


def build_initial_teams():
    teams = []
    for order, ((captain, member, name), code) in enumerate(zip(INITIAL_ROSTER, INITIAL_CODES), 1):
        teams.append({"id": uuid.uuid4().hex[:12], "name": name,
                      "players": [captain, member], "mars_points": [0, 0],
                      "checked_in": True, "eligibility_checked": True, "order": order,
                      "play_code": code})
    return teams
