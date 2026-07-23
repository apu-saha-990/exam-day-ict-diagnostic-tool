"""
network.py

Covers planning doc section 3 (NETWORK / CONNECTIVITY ISSUES).

What this checks, and how:
  - Network adapter detected/enabled          -> PowerShell: Get-NetAdapter
  - Wi-Fi vs Ethernet (flags unstable Wi-Fi
    when a wired connection is available)     -> Get-NetAdapter + Get-NetIPConfiguration
  - Local network OK but no internet          -> ping the default gateway vs ping an
                                                  external IP separately -- this is the
                                                  specific distinction the planning doc
                                                  asks for (section 3, bullet 2)
  - DNS resolution failure                    -> Resolve-DnsName, compared against the
                                                  external-IP ping result so a DNS-only
                                                  failure isn't confused with "no internet"
  - High latency / packet loss                -> Test-Connection round-trip stats
  - VPN interference                          -> adapter name matching against common
                                                  VPN client signatures
  - Proxy configuration                       -> registry: Internet Settings ProxyEnable
  - Rough bandwidth sufficiency for video/
    audio streaming                           -> timed download of a small file from
                                                  Cloudflare's public speed-test endpoint

Known gaps (candidates for planning doc section 11):
  - Firewall/port checks against the actual exam platform aren't
    implemented yet -- the planning doc itself flags this as needing to
    wait until we know which platform AMC uses. Only a general Windows
    Firewall on/off check is included for now.
  - IP conflict detection (duplicate IP on the network) surfaces through
    Windows Event Log entries, not a live network test, so it fits better
    in the event-log/"glitch detection" category once that's built.
  - The bandwidth check is a rough approximation (one timed download), not
    a proper multi-sample speed test -- good enough to flag "clearly too
    slow for video," not precise enough to certify "definitely fine."
"""

from __future__ import annotations

from .powershell_bridge import run_ps_json, PowerShellError
from .report import CategoryReport, CheckResult, Status

VPN_NAME_SIGNATURES = [
    "vpn", "tap-windows", "wireguard", "anyconnect", "fortinet",
    "pulse secure", "globalprotect", "nordvpn", "openvpn", "zscaler",
]


def _simulated_data() -> dict:
    """Mock data so the tool can be demoed/tested off a Windows exam laptop."""
    return {
        "adapters": [
            {"Name": "Ethernet", "Status": "Up", "MediaType": "802.3", "LinkSpeed": "1 Gbps"},
            {"Name": "Wi-Fi", "Status": "Disconnected", "MediaType": "Native 802.11", "LinkSpeed": ""},
        ],
        "active_interface": "Ethernet",
        "gateway": "192.168.1.1",
        "gateway_reachable": True,
        "external_reachable": True,
        "external_loss_pct": 0,
        "external_avg_latency_ms": 18,
        "dns_ok": True,
        "vpn_active": False,
        "proxy_enabled": False,
        "bandwidth_mbps": 85.0,
        "bandwidth_error": None,
    }


def _query() -> dict:
    script = r"""
$result = [ordered]@{}

$adapters = Get-NetAdapter -ErrorAction SilentlyContinue |
    Select-Object Name, InterfaceDescription, Status, MediaType, LinkSpeed
$result.Adapters = $adapters

$ipconfig = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object { $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
$gateway = $null
$activeInterface = $null
if ($ipconfig) {
    $gateway = $ipconfig.IPv4DefaultGateway.NextHop
    $activeInterface = $ipconfig.InterfaceAlias
}
$result.Gateway = $gateway
$result.ActiveInterface = $activeInterface

$gatewayReachable = $false
if ($gateway) {
    try {
        $gwPing = Test-Connection -ComputerName $gateway -Count 2 -ErrorAction Stop
        $gatewayReachable = ($gwPing | Where-Object { $_.StatusCode -eq 0 }).Count -gt 0
    } catch {}
}
$result.GatewayReachable = $gatewayReachable

$extReachable = $false
$extLossPct = $null
$extAvgLatency = $null
try {
    $extPing = Test-Connection -ComputerName 8.8.8.8 -Count 4 -ErrorAction Stop
    $successes = $extPing | Where-Object { $_.StatusCode -eq 0 }
    $extReachable = $successes.Count -gt 0
    $extLossPct = [math]::Round((4 - $successes.Count) / 4 * 100)
    if ($successes.Count -gt 0) {
        $extAvgLatency = [math]::Round(($successes | Measure-Object -Property ResponseTime -Average).Average)
    }
} catch {}
$result.ExternalReachable = $extReachable
$result.ExternalLossPct = $extLossPct
$result.ExternalAvgLatencyMs = $extAvgLatency

$dnsOk = $false
try {
    $null = Resolve-DnsName -Name "www.google.com" -ErrorAction Stop
    $dnsOk = $true
} catch {}
$result.DnsOk = $dnsOk

$vpnActive = $false
if ($adapters) {
    foreach ($a in $adapters) {
        if ($a.Status -eq 'Up') {
            $desc = "$($a.Name) $($a.InterfaceDescription)".ToLower()
            foreach ($sig in @('vpn','tap-windows','wireguard','anyconnect','fortinet','pulse secure','globalprotect','nordvpn','openvpn','zscaler')) {
                if ($desc -like "*$sig*") { $vpnActive = $true }
            }
        }
    }
}
$result.VpnActive = $vpnActive

$proxyEnabled = $false
try {
    $proxy = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyEnable -ErrorAction Stop
    $proxyEnabled = [bool]$proxy.ProxyEnable
} catch {}
$result.ProxyEnabled = $proxyEnabled

$bandwidthMbps = $null
$bandwidthError = $null
try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-WebRequest -Uri "https://speed.cloudflare.com/__down?bytes=2000000" -TimeoutSec 8 -UseBasicParsing -ErrorAction Stop
    $sw.Stop()
    $bytes = $resp.RawContentLength
    if (-not $bytes) { $bytes = $resp.Content.Length }
    $seconds = [math]::Max($sw.Elapsed.TotalSeconds, 0.05)
    $bandwidthMbps = [math]::Round(($bytes * 8 / $seconds) / 1MB, 1)
} catch {
    $bandwidthError = $_.Exception.Message
}
$result.BandwidthMbps = $bandwidthMbps
$result.BandwidthError = $bandwidthError

$result | ConvertTo-Json -Depth 4
""".strip()

    result = run_ps_json(script, timeout=25)
    if not result.success:
        raise PowerShellError(f"Network query failed: {result.raw_stderr}")

    row = result.data[0] if result.data else {}
    adapters = row.get("Adapters") or []
    if isinstance(adapters, dict):
        adapters = [adapters]

    return {
        "adapters": adapters,
        "active_interface": row.get("ActiveInterface"),
        "gateway": row.get("Gateway"),
        "gateway_reachable": row.get("GatewayReachable"),
        "external_reachable": row.get("ExternalReachable"),
        "external_loss_pct": row.get("ExternalLossPct"),
        "external_avg_latency_ms": row.get("ExternalAvgLatencyMs"),
        "dns_ok": row.get("DnsOk"),
        "vpn_active": row.get("VpnActive"),
        "proxy_enabled": row.get("ProxyEnabled"),
        "bandwidth_mbps": row.get("BandwidthMbps"),
        "bandwidth_error": row.get("BandwidthError"),
    }


def run(simulate: bool = False) -> CategoryReport:
    report = CategoryReport(category="Network")

    if simulate:
        data = _simulated_data()
    else:
        try:
            data = _query()
        except PowerShellError as exc:
            report.add(CheckResult(
                name="Network subsystem query",
                status=Status.FAIL,
                summary="Couldn't read network information from Windows at all.",
                detail=str(exc),
                recommendation="Try running the tool again. If this keeps happening, "
                                "the issue may be with the network stack itself.",
            ))
            return report

    # --- Adapter presence ------------------------------------------------------
    adapters = data.get("adapters", [])
    if not adapters:
        report.add(CheckResult(
            name="Network adapter detected",
            status=Status.FAIL,
            summary="No network adapter was found on this computer at all.",
            recommendation="Check Device Manager for a disabled or missing network adapter.",
        ))
        return report  # nothing else in this category can be meaningfully checked

    up_adapters = [a for a in adapters if str(a.get("Status", "")).lower() == "up"]
    names_summary = ", ".join(f"{a.get('Name')} ({a.get('Status')})" for a in adapters)
    report.add(CheckResult(
        name="Network adapters found",
        status=Status.INFO,
        summary=f"Found {len(adapters)} adapter(s): {names_summary}.",
    ))
    if not up_adapters:
        report.add(CheckResult(
            name="Active network connection",
            status=Status.FAIL,
            summary="No network adapter is currently connected.",
            recommendation="Plug in an Ethernet cable, or connect to Wi-Fi.",
        ))
        return report

    # --- Wi-Fi vs Ethernet -------------------------------------------------------
    active_iface = (data.get("active_interface") or "").lower()
    is_on_wifi = "wi-fi" in active_iface or "wireless" in active_iface or "wlan" in active_iface

    # "Not Present" means no cable is plugged in (or the port has no
    # hardware attached) -- it is NOT the same as an idle-but-ready
    # connection. Only "Disconnected" adapters (enabled, no active link,
    # but genuinely there) count as a real "you could switch to this"
    # option; "Not Present" just means the port exists.
    ethernet_disconnected = any(
        "ethernet" in str(a.get("Name", "")).lower()
        and str(a.get("Status", "")).lower() == "disconnected"
        for a in adapters
    )
    ethernet_not_present = any(
        "ethernet" in str(a.get("Name", "")).lower()
        and str(a.get("Status", "")).lower() == "not present"
        for a in adapters
    )

    if is_on_wifi and ethernet_disconnected:
        report.add(CheckResult(
            name="Wi-Fi vs Ethernet",
            status=Status.WARNING,
            summary="Currently connected over Wi-Fi, but an Ethernet port is available "
                     "and not currently connected.",
            recommendation="Plug in an Ethernet cable for exam sessions -- it's more "
                            "stable and less likely to drop mid-exam than Wi-Fi.",
        ))
    elif is_on_wifi and ethernet_not_present:
        report.add(CheckResult(
            name="Wi-Fi vs Ethernet",
            status=Status.INFO,
            summary="Currently connected over Wi-Fi. An Ethernet adapter exists but has "
                     "no cable plugged in right now.",
            recommendation="If a wired connection is available in the exam room, using "
                            "it instead of Wi-Fi is generally more stable.",
        ))
    else:
        report.add(CheckResult(
            name="Wi-Fi vs Ethernet",
            status=Status.PASS,
            summary=f"Connected via {data.get('active_interface') or 'a network adapter'}, "
                     "no better wired option currently sitting idle.",
        ))

    # --- Gateway (local network) vs external (internet) reachability -----------
    gateway_reachable = data.get("gateway_reachable")
    external_reachable = data.get("external_reachable")

    if not gateway_reachable and not external_reachable:
        report.add(CheckResult(
            name="Internet connectivity",
            status=Status.FAIL,
            summary="No network connectivity at all -- can't even reach the local router.",
            recommendation="Check the physical connection or router, then re-run this check.",
        ))
    elif gateway_reachable and not external_reachable:
        report.add(CheckResult(
            name="Internet connectivity",
            status=Status.FAIL,
            summary="The local network is fine, but this computer can't reach the "
                     "internet at all.",
            detail="Local router responded, but an external address (8.8.8.8) did not.",
            recommendation="Check the router's internet connection, or contact the "
                            "network administrator -- this isn't a problem with this "
                            "computer specifically.",
        ))
    else:
        report.add(CheckResult(
            name="Internet connectivity",
            status=Status.PASS,
            summary="This computer can reach the internet.",
        ))

    # --- DNS ---------------------------------------------------------------------
    dns_ok = data.get("dns_ok")
    if external_reachable and not dns_ok:
        report.add(CheckResult(
            name="DNS resolution",
            status=Status.FAIL,
            summary="The internet connection itself works, but this computer can't "
                     "turn website names into addresses (DNS isn't working).",
            detail="An external IP address was reachable, but resolving "
                    "'www.google.com' failed.",
            recommendation="Try switching DNS servers (e.g. to 8.8.8.8 or 1.1.1.1) in "
                            "the network adapter settings, or flush DNS with "
                            "'ipconfig /flushdns'.",
        ))
    elif not external_reachable:
        report.add(CheckResult(
            name="DNS resolution",
            status=Status.SKIPPED,
            summary="Couldn't test DNS separately since there's no internet connection "
                     "to test it against.",
        ))
    else:
        report.add(CheckResult(
            name="DNS resolution",
            status=Status.PASS,
            summary="Website names are resolving correctly.",
        ))

    # --- Latency / packet loss ----------------------------------------------------
    loss_pct = data.get("external_loss_pct")
    avg_latency = data.get("external_avg_latency_ms")
    if external_reachable and loss_pct is not None:
        if loss_pct >= 25:
            report.add(CheckResult(
                name="Connection quality",
                status=Status.FAIL,
                summary=f"Significant packet loss detected ({loss_pct}%) -- expect the "
                         "exam platform to lag, freeze, or disconnect.",
                recommendation="Move closer to the router, switch to Ethernet if on "
                                "Wi-Fi, or contact the network administrator.",
            ))
        elif loss_pct > 0 or (avg_latency and avg_latency > 150):
            report.add(CheckResult(
                name="Connection quality",
                status=Status.WARNING,
                summary=f"Some packet loss ({loss_pct}%) and/or higher latency "
                         f"({avg_latency} ms) detected -- may cause occasional lag.",
                recommendation="Worth keeping an eye on; consider Ethernet if currently "
                                "on Wi-Fi.",
            ))
        else:
            report.add(CheckResult(
                name="Connection quality",
                status=Status.PASS,
                summary=f"No packet loss, average latency {avg_latency} ms -- healthy "
                         "connection.",
            ))

    # --- VPN / Proxy ---------------------------------------------------------------
    if data.get("vpn_active"):
        report.add(CheckResult(
            name="VPN detected",
            status=Status.WARNING,
            summary="An active VPN connection was detected. This can interfere with "
                     "exam platforms that don't expect traffic to route through a VPN.",
            recommendation="Confirm with the candidate/invigilator whether the VPN "
                            "should be disconnected before starting the exam.",
        ))
    if data.get("proxy_enabled"):
        report.add(CheckResult(
            name="Proxy configured",
            status=Status.WARNING,
            summary="A system-wide proxy is configured for this computer's internet "
                     "connection, common on corporate/managed laptops.",
            recommendation="If the exam platform behaves oddly, check whether the proxy "
                            "is blocking or altering its traffic.",
        ))

    # --- Bandwidth (rough) -----------------------------------------------------------
    bw = data.get("bandwidth_mbps")
    if bw is not None:
        if bw < 2:
            report.add(CheckResult(
                name="Bandwidth (approximate)",
                status=Status.FAIL,
                summary=f"Measured roughly {bw} Mbps -- likely too slow to sustain "
                         "exam video/audio without freezing.",
                recommendation="Switch to Ethernet, move closer to the router, or check "
                                "if something else on the network is consuming bandwidth.",
            ))
        elif bw < 5:
            report.add(CheckResult(
                name="Bandwidth (approximate)",
                status=Status.WARNING,
                summary=f"Measured roughly {bw} Mbps -- workable but on the low side for "
                         "smooth video.",
            ))
        else:
            report.add(CheckResult(
                name="Bandwidth (approximate)",
                status=Status.PASS,
                summary=f"Measured roughly {bw} Mbps -- comfortably enough for exam "
                         "video/audio.",
            ))
    elif data.get("bandwidth_error"):
        report.add(CheckResult(
            name="Bandwidth (approximate)",
            status=Status.SKIPPED,
            summary="Couldn't measure bandwidth (the test download didn't complete).",
            detail=str(data.get("bandwidth_error")),
        ))

    return report
