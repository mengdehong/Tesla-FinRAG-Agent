import json
from pathlib import Path

import requests


def fetch_tesla_companyfacts() -> dict | None:
    # 1. 构造 URL（CIK 需要补齐 10 位）
    cik = "0001318605"
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    # 2. 构造符合 SEC 规范的 Header（需包含可联系邮箱）
    headers = {
        "User-Agent": "TeslaFinRAG-Agent liamari6zro1xdxf8ds@gmail.com",
        "Accept-Encoding": "gzip, deflate",
    }

    print(f"开始请求特斯拉 XBRL 数据: {url}")

    try:
        # 3. 发起请求并处理常见 HTTP 错误
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # 4. 解析并落盘到指定路径
        facts_data = response.json()

        output_path = Path("data/raw/companyfacts.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(facts_data, indent=2), encoding="utf-8")

        print(f"✅ 下载成功！文件已保存至: {output_path}")
        return facts_data

    except requests.exceptions.RequestException as e:
        print(f"❌ 下载失败: {e}")
        return None


if __name__ == "__main__":
    fetch_tesla_companyfacts()
