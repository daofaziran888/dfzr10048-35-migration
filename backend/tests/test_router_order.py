"""路由注册顺序：/api/signoff 固定前缀必须先于 /api/{module} 动态模块路由。"""
from __future__ import annotations

from app.routers import ROUTERS
from app.routers import resource, signoff


def _paths(router) -> set[str]:
    return {route.path for route in router.routes}


def test_signoff_routes_declared():
    paths = _paths(signoff.router)
    assert "/api/signoff/packs" in paths
    assert "/api/signoff/packs/{pack_no}/verify" in paths
    assert "/api/signoff/packs/{pack_no}/originals/{filename}" in paths


def test_signoff_router_registered_before_module_router():
    assert ROUTERS.index(signoff) < ROUTERS.index(resource)


def test_router_prefix_is_signoff_not_dynamic_module():
    # /api/{module}/{entry_id} 会把 /api/signoff/packs 解析成 entry_id="packs"，
    # 所以固定前缀只能靠先注册来保证命中。
    assert "/api/{module}" in _paths(resource.router)
    assert "/api/signoff/packs" not in _paths(resource.router)
