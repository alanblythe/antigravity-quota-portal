"""
Isolated Antigravity CLI Runner for Multi-User Simulation Testing.

This module provides an IsolatedAgyRunner class that executes `agy` CLI commands
under isolated environment variables (HOME and CLOUDSDK_CONFIG), ensuring no pollution
of the host user's personal session or credentials.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AgyRunResult:
    """Result of an isolated agy CLI execution."""
    exit_code: int
    stdout: str
    stderr: str
    parsed_json: Optional[Dict[str, Any]] = None
    success: bool = False


class IsolatedAgyRunner:
    """Manages execution of agy CLI commands within an isolated user environment."""

    def __init__(
        self,
        user_email: str,
        base_dir: Optional[str] = None,
        project_id: Optional[str] = None,
    ):
        self.user_email = user_email
        self.username = user_email.split("@")[0]
        self.project_id = (
            project_id
            or os.getenv("WORKLOAD_PROJECT_ID")
            or os.getenv("GCP_PROJECT_ID")
            or "my-workload-project"
        )

        test_users_base = base_dir or os.getenv("TEST_USERS_DIR")
        if test_users_base:
            self.user_home = Path(test_users_base) / self.username
        else:
            self.user_home = Path.home() / ".agy_test_users" / self.username

        self.gcloud_config = self.user_home / ".config" / "gcloud"
        self.gemini_dir = self.user_home / ".gemini"
        self.token_path = self.gemini_dir / "antigravity-cli" / "antigravity-oauth-token"

    def setup_directories(self) -> None:
        """Ensure isolated home and config directories exist."""
        self.user_home.mkdir(parents=True, exist_ok=True)
        self.gcloud_config.mkdir(parents=True, exist_ok=True)
        self.gemini_dir.mkdir(parents=True, exist_ok=True)

    def is_authenticated(self) -> bool:
        """Check if the user has active Antigravity OAuth or ADC credentials."""
        has_token = self.token_path.exists()
        has_adc = (self.gcloud_config / "application_default_credentials.json").exists()
        return has_token or has_adc

    def get_env(self) -> Dict[str, str]:
        """Construct the isolated environment dictionary."""
        env = os.environ.copy()
        env["HOME"] = str(self.user_home)
        env["CLOUDSDK_CONFIG"] = str(self.gcloud_config)
        return env

    def run_prompt(
        self,
        prompt: str,
        model: Optional[str] = "gemini-3.7-flash-medium",
        timeout: int = 60,
    ) -> AgyRunResult:
        """
        Execute a single prompt non-interactively using `agy --print`.

        Args:
            prompt: The text prompt to send to Antigravity.
            model: Gemini model identifier (e.g., gemini-3.7-flash-medium).
            timeout: Maximum execution seconds before timeout.

        Returns:
            AgyRunResult containing return code, raw outputs, and parsed response.
        """
        self.setup_directories()
        cmd = [
            "agy",
            "--print",
            prompt,
            "--output-format",
            "json",
        ]
        if self.project_id:
            cmd.extend(["--project", self.project_id])
        if model:
            cmd.extend(["--model", model])


        try:
            process = subprocess.run(
                cmd,
                env=self.get_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            parsed = None
            try:
                parsed = json.loads(process.stdout)
            except Exception:
                pass

            return AgyRunResult(
                exit_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                parsed_json=parsed,
                success=(process.returncode == 0),
            )
        except subprocess.TimeoutExpired as exc:
            return AgyRunResult(
                exit_code=-1,
                stdout=exc.stdout.decode() if exc.stdout else "",
                stderr=f"Timed out after {timeout}s: {exc}",
                success=False,
            )
        except FileNotFoundError:
            return AgyRunResult(
                exit_code=-2,
                stdout="",
                stderr="agy binary not found in PATH",
                success=False,
            )

    def clean_session_history(self) -> None:
        """Remove conversation history while preserving authentication tokens."""
        conv_dir = self.gemini_dir / "antigravity-cli" / "conversations"
        if conv_dir.exists():
            shutil.rmtree(conv_dir)
