class PiStatusbar < Formula
  desc "Pi macOS status bar app with local daemon and session controls"
  homepage "https://github.com/jademind/pi-statusbar"
  url "https://github.com/jademind/pi-statusbar/archive/refs/tags/v0.1.9.tar.gz"
  sha256 "d0880cfa22f5b979f2234c6ba695ac19b6c7d9480f557a4fd3f4981af1430f5c"
  version "0.1.9"
  license "MIT"
  head "https://github.com/jademind/pi-statusbar.git", branch: "main"

  depends_on :macos
  depends_on "python@3.12"
  depends_on "swift"

  def install
    libexec.install Dir["*"]
    ENV["SWIFTPM_DISABLE_SANDBOX"] = "1"

    cd libexec do
      system "swift", "build", "--disable-sandbox", "-c", "release", "--product", "PiStatusBar"
      bin.install ".build/release/PiStatusBar"
    end

    (bin/"statusdctl").write_env_script libexec/"daemon/statusdctl", PI_STATUSBAR_ROOT: libexec
    (bin/"statusd-service").write_env_script libexec/"daemon/statusd-service", PI_STATUSBAR_ROOT: libexec
    (bin/"statusbar-app-service").write_env_script libexec/"daemon/statusbar-app-service", PI_STATUSBAR_ROOT: libexec
    (bin/"statusbar-setup").write_env_script libexec/"daemon/statusbar-setup", PI_STATUSBAR_ROOT: libexec
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

  def caveats
    <<~EOS
      Quick setup (start now + enable at login):
        statusbar-setup enable

      Start now only (no login autostart):
        statusbar-setup enable --login no

      Stop now:
        statusbar-setup stop

      Stop and remove login autostart:
        statusbar-setup stop --remove yes

      Verify:
        statusbar-setup status
    EOS
  end

  test do
    output = shell_output("#{bin}/statusdctl status 2>&1", 1)
    assert_match "pi-statusd", output
  end
end
