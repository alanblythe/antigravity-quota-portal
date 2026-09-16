#!/usr/bin/env python3
"""Capture a high-resolution screenshot of the Antigravity Quota Portal dashboard using headless Chrome."""

import os
import subprocess
import sys
import time

import httpx
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

PORT = 8089
OUTPUT_IMAGE = os.path.abspath(os.path.join(os.path.dirname(__file__), "../docs/images/portal_dashboard.png"))


def capture():
    os.makedirs(os.path.dirname(OUTPUT_IMAGE), exist_ok=True)

    # 1. Start demo portal server with 3 users
    env = os.environ.copy()
    env["PORT"] = str(PORT)
    env["USE_MOCK_SERVICES"] = "true"

    print(f"==> Launching demo portal server on http://localhost:{PORT}...")
    server_proc = subprocess.Popen(
        [sys.executable, "scripts/run_local_demo.py"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        # Wait for portal health endpoint
        ready = False
        for _ in range(30):
            try:
                r = httpx.get(f"http://localhost:{PORT}/api/health", timeout=1.0)
                if r.status_code == 200:
                    ready = True
                    print("✅ Portal server is running.")
                    break
            except Exception:
                time.sleep(0.3)

        if not ready:
            print("❌ Server failed to start.")
            sys.exit(1)

        # 2. Configure Chrome headless options
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1440,960")
        options.add_argument("--force-device-scale-factor=1")

        print("==> Launching headless Chrome and navigating to dashboard...")
        driver = webdriver.Chrome(options=options)
        try:
            driver.get(f"http://localhost:{PORT}/")

            # 1. Main Dashboard
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            time.sleep(1.5)
            driver.save_screenshot(OUTPUT_IMAGE)
            print(f"🎉 Saved dashboard screenshot: {OUTPUT_IMAGE} ({os.path.getsize(OUTPUT_IMAGE)/1024:.1f} KB)")

            # 2. Audit Trail Modal
            audit_btn = driver.find_element(By.XPATH, "//button[contains(., 'Audit Trail')]")
            audit_btn.click()
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']//table")))
            time.sleep(1.0)
            audit_img = os.path.abspath(os.path.join(os.path.dirname(__file__), "../docs/images/portal_audit_log.png"))
            driver.save_screenshot(audit_img)
            print(f"🎉 Saved audit log screenshot: {audit_img} ({os.path.getsize(audit_img)/1024:.1f} KB)")
            close_btn = driver.find_element(By.XPATH, "//div[@role='dialog']//button[contains(., 'Close')]")
            close_btn.click()
            time.sleep(0.5)

            # 3. Pricing Matrix Modal
            pricing_btn = driver.find_element(By.XPATH, "//button[contains(., 'Pricing Matrix')]")
            pricing_btn.click()
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']//table")))
            time.sleep(1.0)
            pricing_img = os.path.abspath(os.path.join(os.path.dirname(__file__), "../docs/images/portal_pricing_matrix.png"))
            driver.save_screenshot(pricing_img)
            print(f"🎉 Saved pricing matrix screenshot: {pricing_img} ({os.path.getsize(pricing_img)/1024:.1f} KB)")
            cancel_btn = driver.find_element(By.XPATH, "//div[@role='dialog']//button[contains(., 'Cancel')]")
            cancel_btn.click()
            time.sleep(0.5)

            # 4. Settings Modal
            settings_btn = driver.find_element(By.XPATH, "//button[contains(., 'Settings')]")
            settings_btn.click()
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']//input")))
            time.sleep(1.0)
            settings_img = os.path.abspath(os.path.join(os.path.dirname(__file__), "../docs/images/portal_settings.png"))
            driver.save_screenshot(settings_img)
            print(f"🎉 Saved settings screenshot: {settings_img} ({os.path.getsize(settings_img)/1024:.1f} KB)")
            cancel_btn = driver.find_element(By.XPATH, "//div[@role='dialog']//button[contains(., 'Cancel')]")
            cancel_btn.click()
            time.sleep(0.5)

        finally:
            driver.quit()

    finally:
        print("==> Shutting down demo server...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=3)
        except Exception:
            server_proc.kill()


if __name__ == "__main__":
    capture()
