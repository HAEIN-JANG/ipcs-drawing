# 보조 스크립트로 DB를 직접 바꾼 뒤 웹 앱의 조회 캐시(30분)를 바로 비우도록 알리는 도우미
import os
import urllib.request

APP_URL = os.environ.get("IPCS_DRAWING_URL", "https://ipcs-drawing-v1.onrender.com")


def clear_app_cache():
    # 실패해도 DB 반영은 끝났으므로 경고만 출력한다(최대 30분 뒤 자동 갱신).
    req = urllib.request.Request(f"{APP_URL}/api/cache/clear", method="POST",
                                 headers={"X-Write-Password": os.environ.get("WRITE_PASSWORD", "")})
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            print(f"웹 앱 캐시 비움: {res.status}")
    except Exception as e:
        print(f"웹 앱 캐시 비우기 실패(최대 30분 뒤 자동 반영): {e}")
