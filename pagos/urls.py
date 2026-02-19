from django.urls import path

from .views import (
    HomeView,
    compra_create_view,
    compra_edit_view,
    compra_flujo_view,
    compras_operativas_view,
    productor_edit_view,
    productores_catalogo_view,
    registro_view,
)

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("registro/", registro_view, name="registro"),
    path("compras/", compras_operativas_view, name="compras_operativas"),
    path("compras/nueva/", compra_create_view, name="compra_create"),
    path("compras/<int:compra_id>/flujo/", compra_flujo_view, name="compra_flujo"),
    path("compras/<int:compra_id>/editar/", compra_edit_view, name="compra_edit"),
    path("productores/", productores_catalogo_view, name="productores_catalogo"),
    path("productores/<int:productor_id>/editar/", productor_edit_view, name="productor_edit"),
]
