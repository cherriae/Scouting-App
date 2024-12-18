from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from .team_utils import TeamManager


team_routes = Blueprint('team', __name__)

@team_routes.route('/create', methods=['POST'])
@login_required
async def create_team():
    """Create a new team"""
    data = request.get_json()
    team_number = data.get('team_number')
    
    if not team_number:
        return jsonify({'success': False, 'message': 'Team number is required'}), 400

    team_manager = TeamManager(current_app.config['MONGO_URI'])
    success, result = await team_manager.create_team(team_number, str(current_user.id))

    if success:
        return jsonify({'success': True, 'team': result.to_dict()}), 201
    return jsonify({'success': False, 'message': result}), 400

@team_routes.route('/join', methods=['POST'])
@login_required
async def join_team():
    """Join a team using join code"""
    data = request.get_json()
    join_code = data.get('join_code')
    
    if not join_code:
        return jsonify({'success': False, 'message': 'Join code is required'}), 400

    team_manager = TeamManager(current_app.config['MONGO_URI'])
    success, result = await team_manager.join_team(str(current_user.id), join_code)

    if success:
        return jsonify({'success': True, 'team': result.to_dict()}), 200
    return jsonify({'success': False, 'message': result}), 400

@team_routes.route('/leave/<int:team_number>', methods=['POST'])
@login_required
async def leave_team(team_number):
    """Leave a team"""
    team_manager = TeamManager(current_app.config['MONGO_URI'])
    success, message = await team_manager.leave_team(str(current_user.id), team_number)

    return jsonify({'success': success, 'message': message}), 200 if success else 400

@team_routes.route('/<int:team_number>/members', methods=['GET'])
@login_required
def get_team_members(team_number):
    """Get all members of a team"""
    team_manager = TeamManager(current_app.config['MONGO_URI'])
    members = team_manager.get_team_members(team_number)
    
    return jsonify({
        'success': True,
        'members': [member.to_dict() for member in members]
    }), 200

@team_routes.route('/<int:team_number>/admin/add', methods=['POST'])
@login_required
async def add_admin(team_number):
    """Add a new admin to the team"""
    data = request.get_json()
    user_id = data.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID is required'}), 400

    team_manager = TeamManager(current_app.config['MONGO_URI'])
    success, message = await team_manager.add_admin(team_number, user_id, str(current_user.id))

    return jsonify({'success': success, 'message': message}), 200 if success else 400

@team_routes.route('/<int:team_number>/assignments', methods=['POST'])
@login_required
async def create_assignment(team_number):
    """Create a new assignment for the team"""
    data = request.get_json()
    
    team_manager = TeamManager(current_app.config['MONGO_URI'])
    success, result = await team_manager.create_assignment(team_number, str(current_user.id), data)

    if success:
        return jsonify({'success': True, 'assignment': result.to_dict()}), 201
    return jsonify({'success': False, 'message': result}), 400

@team_routes.route('/assignments/<assignment_id>/status', methods=['PUT'])
@login_required
async def update_assignment_status(assignment_id):
    """Update assignment status"""
    data = request.get_json()
    new_status = data.get('status')
    
    if not new_status:
        return jsonify({'success': False, 'message': 'Status is required'}), 400

    team_manager = TeamManager(current_app.config['MONGO_URI'])
    success, message = await team_manager.update_assignment_status(
        assignment_id, str(current_user.id), new_status
    )

    return jsonify({'success': success, 'message': message}), 200 if success else 400
