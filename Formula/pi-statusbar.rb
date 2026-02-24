class PiStatusbar < Formula
  desc "Pi macOS status bar app with local daemon and session controls"
  homepage "https://github.com/jademind/pi-statusbar"
  url "https://github.com/jademind/pi-statusbar/archive/refs/tags/v0.1.5.tar.gz"
  sha256 "b546ee522cc88d710b987325551fa4c7206038bda3afce01aadd7294fcb7ab9e"
  version "0.1.5"
  license "MIT"
  head "https://github.com/jademind/pi-statusbar.git", branch: "main"

  depends_on :macos
  depends_on "python@3.12"
  depends_on "swift"

  def install
    libexec.install Dir["*"]

    (bin/"PiStatusBar").write <<~EOS
      #!/usr/bin/env bash
      set -euo pipefail
      exec swift run --package-path "#{opt_libexec}" PiStatusBar "$@"
    EOS

    (bin/"statusdctl").write_env_script libexec/"daemon/statusdctl", PI_STATUSBAR_ROOT: libexec
    (bin/"statusd-service").write_env_script libexec/"daemon/statusd-service", PI_STATUSBAR_ROOT: libexec
    (bin/"statusbar-app-service").write_env_script libexec/"daemon/statusbar-app-service", PI_STATUSBAR_ROOT: libexec
  end

  service do
    run [
      Formula["python@3.12"].opt_bin/"python3.12",
      opt_libexec/"daemon/pi_statusd.py"
    ]
    keep_alive true
    run_type :immediate
    working_dir var
    log_path var/"log/pi-statusd.log"
    error_log_path var/"log/pi-statusd.log"
  end

  test do
    output = shell_output("#{bin}/statusdctl status 2>&1", 1)
    assert_match "pi-statusd", output
  end
end
