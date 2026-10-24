"""复制本文件为 secrets.py，填入你的真实配置。

secrets.py 已被 .gitignore 排除，不会被提交到 GitHub。
天气由电脑端读取（只用到 SENV_KEY / SENV_CITY），板子本身不联网，
Wi-Fi 字段留空即可。
"""

# 板子不联网，以下两项保留占位即可
SSID = ""
PASSWORD = ""

# 心知天气 https://www.seniverse.com/ （v3 接口使用私钥）
# 免费套餐：紫外线可用 life/suggestion 获取；
# 降雨只有降水量 precip(mm)，没有降水概率(%)
SENV_KEY = "your-seniverse-api-key"

# 城市拼音或城市 ID，例如 kunming / shanghai / WX4FB11FYM4C9
SENV_CITY = "kunming"
