# from app import app
# from api.v1.routes.domain_list_router import router as domain_list_router


# def _router_already_registered() -> bool:
#     return any(
#         getattr(route, "path", "").startswith("/domain-lists")
#         for route in app.router.routes
#     )


# if not _router_already_registered():
    
#     app.include_router(domain_list_router)
