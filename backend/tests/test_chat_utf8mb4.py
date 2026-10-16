import pytest
from sqlalchemy.dialects import mysql
from app.models import Conversation, ConversationMessage
from scripts.migrate_chat_utf8mb4 import modification

@pytest.mark.parametrize('model,names', [(Conversation,['title']), (ConversationMessage,['content','reasoning_content','routing_reasoning_content'])])
def test_mysql_chat_text_explicitly_supports_four_byte_unicode(model,names):
    for name in names:
        rendered=str(model.__table__.c[name].type.compile(dialect=mysql.dialect()))
        assert 'CHARACTER SET utf8mb4' in rendered
        assert 'COLLATE utf8mb4_general_ci' in rendered

@pytest.mark.parametrize('nullable',[True,False])
def test_upgrade_preserves_nullability_and_is_idempotent(nullable):
    row={'COLUMN_TYPE':'text','IS_NULLABLE':'YES' if nullable else 'NO','COLUMN_DEFAULT':None,'EXTRA':'','CHARACTER_SET_NAME':'utf8mb3','COLLATION_NAME':'utf8mb3_general_ci'}
    change=modification('content',row,('text',nullable))
    assert change.endswith(' NULL' if nullable else ' NOT NULL')
    assert 'utf8mb4' in change
    row['CHARACTER_SET_NAME']='utf8mb4'
    assert modification('content',row,('text',nullable)) is None

def test_unexpected_schema_refuses_ddl():
    row={'COLUMN_TYPE':'varchar(80)','IS_NULLABLE':'NO','COLUMN_DEFAULT':None,'EXTRA':'','CHARACTER_SET_NAME':'utf8mb3','COLLATION_NAME':'utf8mb3_general_ci'}
    with pytest.raises(RuntimeError): modification('content',row,('text',False))
