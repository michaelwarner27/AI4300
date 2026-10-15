"""
Test suite for Sudoku Board implementation.
Tests all methods defined in board-req.md using the example board from Example-Board.py.
"""

import unittest
from Example_Board import sudoku_puzzle
from board import Board, Tile


class TestBoardInitialization(unittest.TestCase):
    """Test Board initialization and basic setup."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_board_dimensions(self):
        """Test that board is initialized as 9x9."""
        self.assertEqual(len(self.board.grid), 9)
        for row in self.board.grid:
            self.assertEqual(len(row), 9)
    
    def test_tiles_are_tile_objects(self):
        """Test that all grid positions contain Tile objects."""
        for row in self.board.grid:
            for tile in row:
                self.assertIsInstance(tile, Tile)
    
    def test_initial_values_loaded(self):
        """Test that starter board values are correctly loaded."""
        # Check a few known values from sudoku_puzzle
        self.assertEqual(self.board.readTile(0, 0), 5)
        self.assertEqual(self.board.readTile(0, 1), 3)
        self.assertEqual(self.board.readTile(1, 0), 6)
        self.assertEqual(self.board.readTile(1, 3), 1)
    
    def test_empty_tiles_initialized_as_none(self):
        """Test that empty tiles (0 in input) are initialized as None."""
        # Position (0, 2) is 0 in sudoku_puzzle, should be None
        self.assertIsNone(self.board.readTile(0, 2))
        # Position (0, 3) is 0 in sudoku_puzzle, should be None
        self.assertIsNone(self.board.readTile(0, 3))
    
    def test_starter_tiles_not_writable(self):
        """Test that tiles initialized with values are not writable."""
        # (0, 0) has value 5, should not be writable
        self.assertFalse(self.board.grid[0][0].writable)
        # (1, 0) has value 6, should not be writable
        self.assertFalse(self.board.grid[1][0].writable)
    
    def test_empty_tiles_are_writable(self):
        """Test that empty tiles are writable."""
        # (0, 2) is empty, should be writable
        self.assertTrue(self.board.grid[0][2].writable)
        # (0, 3) is empty, should be writable
        self.assertTrue(self.board.grid[0][3].writable)
    
    def test_notes_initialized_empty(self):
        """Test that tile notes are initialized as empty lists."""
        for row in self.board.grid:
            for tile in row:
                self.assertEqual(tile.notes, [])
                self.assertIsInstance(tile.notes, list)


class TestTileReadWrite(unittest.TestCase):
    """Test Tile read and write operations."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_read_tile_existing_value(self):
        """Test reading an existing tile value."""
        self.assertEqual(self.board.readTile(0, 0), 5)
        self.assertEqual(self.board.readTile(2, 1), 9)
    
    def test_read_tile_empty(self):
        """Test reading an empty tile."""
        self.assertIsNone(self.board.readTile(0, 2))
    
    def test_write_value_to_writable_tile(self):
        """Test writing a valid value to a writable tile."""
        result = self.board.writeValue(0, 2, 4)
        self.assertTrue(result)
        self.assertEqual(self.board.readTile(0, 2), 4)
    
    def test_write_value_to_non_writable_tile(self):
        """Test that writing to a non-writable tile fails."""
        result = self.board.writeValue(0, 0, 9)
        self.assertFalse(result)
        # Value should not change
        self.assertEqual(self.board.readTile(0, 0), 5)
    
    def test_write_invalid_value_zero(self):
        """Test that writing 0 (invalid value) is rejected."""
        result = self.board.writeValue(0, 2, 0)
        self.assertFalse(result)
        self.assertIsNone(self.board.readTile(0, 2))
    
    def test_write_invalid_value_too_high(self):
        """Test that writing values > 9 is rejected."""
        result = self.board.writeValue(0, 2, 10)
        self.assertFalse(result)
        self.assertIsNone(self.board.readTile(0, 2))
    
    def test_write_invalid_value_negative(self):
        """Test that writing negative values is rejected."""
        result = self.board.writeValue(0, 2, -1)
        self.assertFalse(result)
        self.assertIsNone(self.board.readTile(0, 2))
    
    def test_write_none_value_rejected(self):
        """Test that writing None is rejected (use eraseTile instead)."""
        self.board.writeValue(0, 2, 4)  # First write a value
        result = self.board.writeValue(0, 2, None) #type:ignore This will raise an error
        self.assertFalse(result)
        # Value should not be erased
        self.assertEqual(self.board.readTile(0, 2), 4)
    
    def test_write_all_valid_values(self):
        """Test writing all valid values 1-9."""
        for value in range(1, 10):
            row, col = 0, 2 + (value - 1) // 9  # Use different columns
            if col < 9:
                result = self.board.writeValue(row, col, value)
                self.assertTrue(result)
                self.assertEqual(self.board.readTile(row, col), value)
    
    def test_erase_tile_writable(self):
        """Test erasing a writable tile."""
        self.board.writeValue(0, 2, 4)  # First write a value
        result = self.board.eraseTile(0, 2)
        self.assertTrue(result)
        self.assertIsNone(self.board.readTile(0, 2))
    
    def test_erase_tile_non_writable(self):
        """Test that erasing a non-writable tile fails."""
        result = self.board.eraseTile(0, 0)
        self.assertFalse(result)
        # Value should not change
        self.assertEqual(self.board.readTile(0, 0), 5)
    
    def test_erase_already_empty_tile(self):
        """Test erasing an already empty tile."""
        result = self.board.eraseTile(0, 2)
        self.assertTrue(result)
        self.assertIsNone(self.board.readTile(0, 2))


class TestExistenceChecks(unittest.TestCase):
    """Test checkIfInRow, checkIfInColumn, checkIfInBlock methods."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_check_if_in_row_exists(self):
        """Test checking for a value that exists in a row."""
        # Row 0 has: [5, 3, 0, 0, 7, 0, 0, 0, 0]
        self.assertTrue(self.board.checkIfInRow(0, 5))
        self.assertTrue(self.board.checkIfInRow(0, 3))
        self.assertTrue(self.board.checkIfInRow(0, 7))
    
    def test_check_if_in_row_not_exists(self):
        """Test checking for a value that doesn't exist in a row."""
        # Row 0 does not have 1, 2, 4, 6, 8, 9
        self.assertFalse(self.board.checkIfInRow(0, 1))
        self.assertFalse(self.board.checkIfInRow(0, 2))
        self.assertFalse(self.board.checkIfInRow(0, 4))
    
    def test_check_if_in_row_all_rows(self):
        """Test checking all rows."""
        # Row 1 has: [6, 0, 0, 1, 9, 5, 0, 0, 0]
        self.assertTrue(self.board.checkIfInRow(1, 6))
        self.assertTrue(self.board.checkIfInRow(1, 1))
        self.assertTrue(self.board.checkIfInRow(1, 9))
    
    def test_check_if_in_column_exists(self):
        """Test checking for a value that exists in a column."""
        # Column 0 has: [5, 6, 0, 8, 4, 7, 0, 0, 0]
        self.assertTrue(self.board.checkIfInColumn(0, 5))
        self.assertTrue(self.board.checkIfInColumn(0, 6))
        self.assertTrue(self.board.checkIfInColumn(0, 8))
        self.assertTrue(self.board.checkIfInColumn(0, 4))
        self.assertTrue(self.board.checkIfInColumn(0, 7))
    
    def test_check_if_in_column_not_exists(self):
        """Test checking for a value that doesn't exist in a column."""
        # Column 0 does not have 1, 2, 3, 9
        self.assertFalse(self.board.checkIfInColumn(0, 1))
        self.assertFalse(self.board.checkIfInColumn(0, 2))
        self.assertFalse(self.board.checkIfInColumn(0, 3))
        self.assertFalse(self.board.checkIfInColumn(0, 9))
    
    def test_check_if_in_block_exists(self):
        """Test checking for a value that exists in a block."""
        # Block 0 (top-left 3x3) contains values from positions:
        # (0,0)=5, (0,1)=3, (1,0)=6, (1,3)=1, (1,4)=9, (1,5)=5, (2,1)=9, (2,2)=8
        self.assertTrue(self.board.checkIfInBlock(0, 5))
        self.assertTrue(self.board.checkIfInBlock(0, 3))
        self.assertTrue(self.board.checkIfInBlock(0, 6))
        self.assertTrue(self.board.checkIfInBlock(0, 9))
        self.assertTrue(self.board.checkIfInBlock(0, 8))
    
    def test_check_if_in_block_not_exists(self):
        """Test checking for a value that doesn't exist in a block."""
        # Block 0 does not have 2, 4, 7
        self.assertFalse(self.board.checkIfInBlock(0, 2))
        self.assertFalse(self.board.checkIfInBlock(0, 4))
        self.assertFalse(self.board.checkIfInBlock(0, 7))
    
    def test_check_if_in_all_blocks(self):
        """Test that different blocks can be checked."""
        # Test that we can check blocks 0-8
        for block_num in range(9):
            # This should not raise an error
            result = self.board.checkIfInBlock(block_num, 1)
            self.assertIsInstance(result, bool)


class TestLocateBlock(unittest.TestCase):
    """Test locateBlock method."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_locate_block_top_left(self):
        """Test locating block for top-left corner."""
        self.assertEqual(self.board.locateBlock(0, 0), 0)
        self.assertEqual(self.board.locateBlock(1, 1), 0)
        self.assertEqual(self.board.locateBlock(2, 2), 0)
    
    def test_locate_block_top_middle(self):
        """Test locating block for top-middle."""
        self.assertEqual(self.board.locateBlock(0, 3), 1)
        self.assertEqual(self.board.locateBlock(0, 4), 1)
        self.assertEqual(self.board.locateBlock(0, 5), 1)
        self.assertEqual(self.board.locateBlock(1, 3), 1)
    
    def test_locate_block_top_right(self):
        """Test locating block for top-right."""
        self.assertEqual(self.board.locateBlock(0, 6), 2)
        self.assertEqual(self.board.locateBlock(0, 7), 2)
        self.assertEqual(self.board.locateBlock(0, 8), 2)
        self.assertEqual(self.board.locateBlock(2, 8), 2)
    
    def test_locate_block_middle_left(self):
        """Test locating block for middle-left."""
        self.assertEqual(self.board.locateBlock(3, 0), 3)
        self.assertEqual(self.board.locateBlock(4, 1), 3)
        self.assertEqual(self.board.locateBlock(5, 2), 3)
    
    def test_locate_block_middle_middle(self):
        """Test locating block for middle-middle."""
        self.assertEqual(self.board.locateBlock(3, 3), 4)
        self.assertEqual(self.board.locateBlock(4, 4), 4)
        self.assertEqual(self.board.locateBlock(5, 5), 4)
    
    def test_locate_block_middle_right(self):
        """Test locating block for middle-right."""
        self.assertEqual(self.board.locateBlock(3, 6), 5)
        self.assertEqual(self.board.locateBlock(4, 7), 5)
        self.assertEqual(self.board.locateBlock(5, 8), 5)
    
    def test_locate_block_bottom_left(self):
        """Test locating block for bottom-left."""
        self.assertEqual(self.board.locateBlock(6, 0), 6)
        self.assertEqual(self.board.locateBlock(7, 1), 6)
        self.assertEqual(self.board.locateBlock(8, 2), 6)
    
    def test_locate_block_bottom_middle(self):
        """Test locating block for bottom-middle."""
        self.assertEqual(self.board.locateBlock(6, 3), 7)
        self.assertEqual(self.board.locateBlock(7, 4), 7)
        self.assertEqual(self.board.locateBlock(8, 5), 7)
    
    def test_locate_block_bottom_right(self):
        """Test locating block for bottom-right."""
        self.assertEqual(self.board.locateBlock(6, 6), 8)
        self.assertEqual(self.board.locateBlock(7, 7), 8)
        self.assertEqual(self.board.locateBlock(8, 8), 8)
    
    def test_locate_block_all_positions(self):
        """Test that all positions map to a block 0-8."""
        for row in range(9):
            for col in range(9):
                block = self.board.locateBlock(row, col)
                self.assertGreaterEqual(block, 0)
                self.assertLessEqual(block, 8)


class TestRowValidation(unittest.TestCase):
    """Test validateRow method."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_validate_row_valid(self):
        """Test validating a row with no duplicates."""
        # Most rows in the starter board should be valid
        result = self.board.validateRow(0)
        self.assertTrue(result)
    
    def test_validate_row_with_empty_tiles(self):
        """Test validating a row that has empty tiles."""
        # Row 0 has empty tiles and is valid
        result = self.board.validateRow(0)
        self.assertTrue(result)
    
    def test_validate_row_invalid_duplicate(self):
        """Test validating a row with duplicate values."""
        # Write a duplicate value in row 0
        self.board.writeValue(0, 2, 5)  # 5 is already in (0, 0)
        result = self.board.validateRow(0)
        self.assertFalse(result)
    
    def test_validate_row_all_rows(self):
        """Test validating all rows."""
        for row in range(9):
            result = self.board.validateRow(row)
            # Initial board should be valid
            self.assertIsInstance(result, bool)


class TestColumnValidation(unittest.TestCase):
    """Test validateColumn method."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_validate_column_valid(self):
        """Test validating a column with no duplicates."""
        result = self.board.validateColumn(0)
        self.assertTrue(result)
    
    def test_validate_column_with_empty_tiles(self):
        """Test validating a column that has empty tiles."""
        result = self.board.validateColumn(2)
        self.assertTrue(result)
    
    def test_validate_column_invalid_duplicate(self):
        """Test validating a column with duplicate values."""
        # Write a duplicate value in column 0
        self.board.writeValue(2, 0, 5)  # 5 is already in (0, 0)
        result = self.board.validateColumn(0)
        self.assertFalse(result)
    
    def test_validate_column_all_columns(self):
        """Test validating all columns."""
        for col in range(9):
            result = self.board.validateColumn(col)
            # Initial board should be valid
            self.assertIsInstance(result, bool)


class TestBlockValidation(unittest.TestCase):
    """Test validateBlock method."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_validate_block_valid(self):
        """Test validating a block with no duplicates."""
        result = self.board.validateBlock(0)
        self.assertTrue(result)
    
    def test_validate_block_with_empty_tiles(self):
        """Test validating a block that has empty tiles."""
        result = self.board.validateBlock(0)
        self.assertTrue(result)
    
    def test_validate_block_invalid_duplicate(self):
        """Test validating a block with duplicate values."""
        # Write a duplicate value in block 0
        self.board.writeValue(0, 2, 5)  # 5 is already at (0, 0) in block 0
        result = self.board.validateBlock(0)
        self.assertFalse(result)
    
    def test_validate_block_all_blocks(self):
        """Test validating all blocks."""
        for block in range(9):
            result = self.board.validateBlock(block)
            # Initial board should be valid
            self.assertIsInstance(result, bool)


class TestIncompleteBoard(unittest.TestCase):
    """Test validateIncompleteBoard method."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_validate_incomplete_board_valid(self):
        """Test validating an incomplete board that is valid."""
        result = self.board.validateIncompleteBoard()
        self.assertTrue(result)
    
    def test_validate_incomplete_board_with_invalid_row(self):
        """Test validating incomplete board with invalid row."""
        self.board.writeValue(0, 2, 5)  # Duplicate in row 0
        result = self.board.validateIncompleteBoard()
        self.assertFalse(result)
    
    def test_validate_incomplete_board_with_invalid_column(self):
        """Test validating incomplete board with invalid column."""
        self.board.writeValue(2, 0, 5)  # Duplicate in column 0
        result = self.board.validateIncompleteBoard()
        self.assertFalse(result)
    
    def test_validate_incomplete_board_with_invalid_block(self):
        """Test validating incomplete board with invalid block."""
        self.board.writeValue(0, 2, 5)  # Duplicate in block 0
        result = self.board.validateIncompleteBoard()
        self.assertFalse(result)


class TestPuzzleValidation(unittest.TestCase):
    """Test validatePuzzle method."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_validate_puzzle_incomplete(self):
        """Test validating incomplete puzzle."""
        # Original board has empty tiles, should fail
        result = self.board.validatePuzzle()
        self.assertFalse(result)
    
    def test_validate_puzzle_invalid_values(self):
        """Test validating puzzle with invalid values."""
        self.board.writeValue(0, 2, 5)  # Duplicate
        result = self.board.validatePuzzle()
        self.assertFalse(result)


class TestNotes(unittest.TestCase):
    """Test note management methods."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_append_note(self):
        """Test appending a note to a tile."""
        self.board.appendNote(0, 2, 1)
        notes = self.board.listNotes(0, 2)
        self.assertIn(1, notes)
    
    def test_append_multiple_notes(self):
        """Test appending multiple notes to a tile."""
        self.board.appendNote(0, 2, 1)
        self.board.appendNote(0, 2, 2)
        self.board.appendNote(0, 2, 4)
        notes = self.board.listNotes(0, 2)
        self.assertEqual(len(notes), 3)
        self.assertIn(1, notes)
        self.assertIn(2, notes)
        self.assertIn(4, notes)
    
    def test_list_notes_empty(self):
        """Test listing notes for a tile with no notes."""
        notes = self.board.listNotes(0, 2)
        self.assertEqual(notes, [])
    
    def test_list_notes_after_append(self):
        """Test that listNotes returns the correct notes."""
        self.board.appendNote(0, 2, 5)
        self.board.appendNote(0, 2, 6)
        notes = self.board.listNotes(0, 2)
        self.assertEqual(notes, [5, 6])
    
    def test_notes_different_tiles(self):
        """Test that notes are stored separately for different tiles."""
        self.board.appendNote(0, 2, 1)
        self.board.appendNote(0, 3, 2)
        notes_02 = self.board.listNotes(0, 2)
        notes_03 = self.board.listNotes(0, 3)
        self.assertIn(1, notes_02)
        self.assertIn(2, notes_03)
        self.assertNotIn(2, notes_02)
        self.assertNotIn(1, notes_03)


class TestPrintBoard(unittest.TestCase):
    """Test printBoard method."""
    
    def setUp(self):
        """Initialize a fresh board before each test."""
        self.board = Board(sudoku_puzzle)
    
    def test_print_board_no_error(self):
        """Test that printBoard executes without error."""
        try:
            self.board.printBoard()
        except Exception as e:
            self.fail(f"printBoard() raised {type(e).__name__}: {e}")


if __name__ == '__main__':
    unittest.main()
