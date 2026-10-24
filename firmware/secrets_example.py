"""复制本文件为 secrets.py，填入你的真实配置。

secrets.py 只部署到开发板上，已被 .gitignore 排除，不会被提交到 GitHub。
"""

# 2.4GHz Wi-Fi（ESP32-S3 不支持 5GHz）
SSID = "your-wifi-name"
PASSWORD = "your-wifi-password"

# 心知天气 https://www.seniverse.com/ （v3 接口使用私钥）
# 免费套餐：紫外线可用 life/suggestion 获取；
# 降雨只有降水量 precip(mm)，没有降水概率(%)
SENV_KEY = "your-seniverse-api-key"

# 城市拼音或城市 ID，例如 kunming / shanghai / WX4FB11FYM4C9
SENV_CITY = "kunming"
