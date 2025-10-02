# routes/permissions.py
from flask import Blueprint, render_template, request, jsonify, current_app
from src.models import db, Group, Permission

permissions_bp = Blueprint("permissions", __name__)

# Página de permissões
@permissions_bp.route("/permissions")
def permissions_page():
    grupos = Group.query.all()

    # pega todas as rotas registradas no Flask
    all_routes = []
    for rule in current_app.url_map.iter_rules():
        if "GET" in rule.methods and not rule.arguments:  # só páginas simples
            endpoint = rule.endpoint
            if endpoint not in ["static", "login", "logout", "permissions_page", "save_permissions", "get_permissions"]:
                all_routes.append(endpoint)

    return render_template("permissions.html", grupos=grupos, rotas=all_routes)


# Retorna permissões já salvas para um grupo
@permissions_bp.route("/get_permissions/<int:group_id>")
def get_permissions(group_id):
    permissoes = Permission.query.filter_by(group_id=group_id).all()
    return jsonify([
        {"page": p.page, "can_view": p.can_view} for p in permissoes
    ])


# Salva permissões
@permissions_bp.route("/save_permissions", methods=["POST"])
def save_permissions():
    data = request.json
    group_id = data.get("group_id")
    updates = data.get("permissions", [])

    for u in updates:
        perm = Permission.query.filter_by(group_id=group_id, page=u["page"]).first()
        if perm:
            perm.can_view = u["can_view"]
        else:
            new_perm = Permission(group_id=group_id, page=u["page"], can_view=u["can_view"])
            db.session.add(new_perm)

    db.session.commit()
    return jsonify({"status": "ok"})
