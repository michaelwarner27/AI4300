"""
Sudoku Board implementation with Tile objects.

This module provides a complete implementation of a Sudoku board with validation
methods for rows, columns, and blocks, as well as tile management functionality.
"""


class Tile:
    """
    Represents a single tile in a Sudoku board.
    
    Attributes:
        value (int | None): The current value (1-9) or None for empty tiles
        writable (bool): Whether this tile can be modified by the user
        notes (list[int]): List of possible values for this tile
    """
    
    def __init__(self, value: int | None = None, writable: bool = True):
        """
        Initialize a Tile.
        
        Args:
            value: Initial value (1-9) or None for empty tiles
            writable: Whether the tile is user-modifiable
        """
        self.value = value
        self.writable = writable
        self.notes = []


class Board:
    """
    Represents a 9x9 Sudoku board.
    
    Manages a grid of Tile objects and provides methods for validation,
    reading/writing values, and managing user notes.
    """
    
    def __init__(self, board: list[list[int]]) -> None:
         """
         Initialize the Sudoku board from a starter configuration.
         
         Args:
             board: A 9x9 list of lists containing integers 0-9,
                    where 0 represents empty tiles.
         """
         self.grid = []
         
         # Initialize the 9x9 grid with Tile objects
         for row in range(9):
             grid_row = []
             for col in range(9):
                 value = board[row][col]
                 # Convert 0 to None for empty tiles
                 if value == 0:
                     tile = Tile(value=None, writable=True)
                 else:
                     # Tiles with initial values are not writable
                     tile = Tile(value=value, writable=False)
                 grid_row.append(tile)
             self.grid.append(grid_row)
         
         # Pre-populate notes for all empty tiles with their candidates
         for row in range(9):
             for col in range(9):
                 if self.grid[row][col].value is None:
                     candidates = self.getCandidates(row, col)
                     self.grid[row][col].notes.extend(candidates)

    def readTile(self, row: int, column: int) -> int | None:
        """
        Read the value of a tile at the specified location.
        
        Args:
            row: Row index (0-8)
            column: Column index (0-8)
            
        Returns:
            The tile's value (1-9) or None if empty
        """
        return self.grid[row][column].value
    
    def writeValue(self, row: int, column: int, value: int) -> bool:
        """
        Write a value to a tile if it is writable.
        
        Args:
            row: Row index (0-8)
            column: Column index (0-8)
            value: Value to write (must be 1-9)
            
        Returns:
            True if write was successful, False otherwise
        """
        tile = self.grid[row][column]
        
        # Check if tile is writable
        if not tile.writable:
            return False
        
        # Reject None values
        if value is None:
            return False
        
        # Reject non-integer values or values outside 1-9
        if not isinstance(value, int) or value < 1 or value > 9:
            return False
        
        # Write the value
        tile.value = value
        return True
    
    def eraseTile(self, row: int, column: int) -> bool:
         """
         Erase a tile (set value to None) if it is writable.
         
         Args:
             row: Row index (0-8)
             column: Column index (0-8)
             
         Returns:
             True if erase was successful, False otherwise
         """
         tile = self.grid[row][column]
         
         # Check if tile is writable
         if not tile.writable:
             return False
         
         # Erase the value
         tile.value = None
         return True
     
    def getCandidates(self, row: int, column: int) -> list[int]:
         """
         Get all legally placeable values for a tile based on Sudoku constraints.
         
         Args:
             row: Row index (0-8)
             column: Column index (0-8)
         
         Returns:
             List of integers (1-9) that are valid candidates for this tile.
             Returns empty list if tile is already filled.
         """
         if self.grid[row][column].value is not None:
             return []  # Tile already filled
         
         candidates = []
         block = self.locateBlock(row, column)
         for value in range(1, 10):
             if not (self.checkIfInRow(row, value) or 
                     self.checkIfInColumn(column, value) or 
                     self.checkIfInBlock(block, value)):
                 candidates.append(value)
         return candidates
     
    def appendNote(self, row: int, column: int, values: list[int]) -> None:
         """
         Append notes to a tile's notes list.
         
         Args:
             row: Row index (0-8)
             column: Column index (0-8)
             values: List of values to append to notes
         """
         tile = self.grid[row][column]
         tile.notes.extend(values)
    
    def listNotes(self, row: int, column: int) -> list[int]:
         """
         Get the notes list for a tile.
         
         Args:
             row: Row index (0-8)
             column: Column index (0-8)
             
         Returns:
             List of note values for the tile
         """
         return self.grid[row][column].notes
    
    def removeNote(self, row: int, column: int, values: list[int]) -> bool:
        """
        Remove notes from a tile's notes list.
        
        Args:
            row: Row index (0-8)
            column: Column index (0-8)
            values: List of values to remove from notes
            
        Returns:
            True if all values were successfully removed, False if any note didn't exist in the list
        """
        tile = self.grid[row][column]
        all_removed = True
        for value in values:
            try:
                tile.notes.remove(value)
            except ValueError:
                all_removed = False
        return all_removed
     
    def checkIfInRow(self, row: int, value: int) -> bool:
        """
        Check if a value already exists in a row.
        
        Args:
            row: Row index (0-8)
            value: Value to check (1-9)
            
        Returns:
            True if value exists in row, False otherwise
        """
        for col in range(9):
            if self.grid[row][col].value == value:
                return True
        return False
    
    def checkIfInColumn(self, column: int, value: int) -> bool:
        """
        Check if a value already exists in a column.
        
        Args:
            column: Column index (0-8)
            value: Value to check (1-9)
            
        Returns:
            True if value exists in column, False otherwise
        """
        for row in range(9):
            if self.grid[row][column].value == value:
                return True
        return False
    
    def checkIfInBlock(self, block: int, value: int) -> bool:
        """
        Check if a value already exists in a block.
        
        Args:
            block: Block index (0-8)
            value: Value to check (1-9)
            
        Returns:
            True if value exists in block, False otherwise
        """
        # Calculate the top-left corner of the block
        block_row = (block // 3) * 3
        block_col = (block % 3) * 3
        
        # Check all 9 tiles in the block
        for row in range(block_row, block_row + 3):
            for col in range(block_col, block_col + 3):
                if self.grid[row][col].value == value:
                    return True
        return False

    def checkTileCandidate(self, row: int, column: int, candidate: int) -> bool:
        if self.checkIfInRow(row, candidate) or self.checkIfInColumn(column, candidate) or self.checkIfInBlock(self.locateBlock(row,column), candidate):
            return False
        return True

    def locateBlock(self, row: int, column: int) -> int:
        """
        Determine which block (0-8) a tile belongs to.
        
        Args:
            row: Row index (0-8)
            column: Column index (0-8)
            
        Returns:
            Block index (0-8)
        """
        block_row = row // 3
        block_col = column // 3
        return block_row * 3 + block_col
    
    def validateRow(self, row: int) -> bool:
        """
        Validate that a row has no duplicate non-empty values.
        
        Args:
            row: Row index (0-8)
            
        Returns:
            True if row is valid, False if duplicates found
        """
        seen_values = set()
        for col in range(9):
            value = self.grid[row][col].value
            # Skip empty tiles (None)
            if value is None:
                continue
            # Check for duplicates
            if value in seen_values:
                return False
            seen_values.add(value)
        return True
    
    def validateColumn(self, column: int) -> bool:
        """
        Validate that a column has no duplicate non-empty values.
        
        Args:
            column: Column index (0-8)
            
        Returns:
            True if column is valid, False if duplicates found
        """
        seen_values = set()
        for row in range(9):
            value = self.grid[row][column].value
            # Skip empty tiles (None)
            if value is None:
                continue
            # Check for duplicates
            if value in seen_values:
                return False
            seen_values.add(value)
        return True
    
    def validateBlock(self, block: int) -> bool:
        """
        Validate that a block has no duplicate non-empty values.
        
        Args:
            block: Block index (0-8)
            
        Returns:
            True if block is valid, False if duplicates found
        """
        # Calculate the top-left corner of the block
        block_row = (block // 3) * 3
        block_col = (block % 3) * 3
        
        seen_values = set()
        # Check all 9 tiles in the block
        for row in range(block_row, block_row + 3):
            for col in range(block_col, block_col + 3):
                value = self.grid[row][col].value
                # Skip empty tiles (None)
                if value is None:
                    continue
                # Check for duplicates
                if value in seen_values:
                    return False
                seen_values.add(value)
        return True
    
    def validateIncompleteBoard(self) -> bool:
        """
        Validate the entire board while allowing empty tiles.
        
        Checks all rows, columns, and blocks for duplicate values.
        Returns False if any duplicates are found.
        
        Returns:
            True if board is valid, False if any duplicates found
        """
        # Validate all rows
        for row in range(9):
            if not self.validateRow(row):
                return False
        
        # Validate all columns
        for col in range(9):
            if not self.validateColumn(col):
                return False
        
        # Validate all blocks
        for block in range(9):
            if not self.validateBlock(block):
                return False
        
        return True
    
    def validatePuzzle(self) -> bool:
        """
        Validate that the puzzle is complete and valid.
        
        Checks that the board has no empty tiles and all rows, columns,
        and blocks have no duplicate values.
        
        Returns:
            True if puzzle is complete and valid, False otherwise
        """
        # First check if the board is valid
        if not self.validateIncompleteBoard():
            return False
        
        # Check that there are no empty tiles
        for row in range(9):
            for col in range(9):
                if self.grid[row][col].value is None:
                    return False
        
        return True
    
    def printBoard(self) -> None:
        """
        Print the board to the terminal in a human-readable format.
        
        Displays the 9x9 grid with separators for visual clarity.
        Uses '.' to represent empty tiles and numbers 1-9 for filled tiles.
        """
        print("\n   0 1 2 | 3 4 5 | 6 7 8")
        print("   ------+-------+------")
        
        for row in range(9):
            # Print row number and values
            print(f"{row} | ", end="")
            
            for col in range(9):
                value = self.grid[row][col].value
                # Display '.' for empty tiles, or the value itself
                display_value = "." if value is None else str(value)
                print(display_value, end=" ")
                
                # Add separators between blocks
                if col == 2 or col == 5:
                    print("| ", end="")
            
            print()
            
            # Add separators between block rows
            if row == 2 or row == 5:
                print("  ------+-------+------")
        
        print()

    def readRow(self, row: int) -> list[int | None]:
        """
        Read all tile values in a row.
        
        Args:
            row: Row index (0-8)
            
        Returns:
            List of 9 values (1-9 or None for empty tiles) in left-to-right order
        """
        return [self.grid[row][col].value for col in range(9)]
    
    def readColumn(self, column: int) -> list[int | None]:
        """
        Read all tile values in a column.
        
        Args:
            column: Column index (0-8)
            
        Returns:
            List of 9 values (1-9 or None for empty tiles) in top-to-bottom order
        """
        return [self.grid[row][column].value for row in range(9)]
    
    def readBlock(self, block: int) -> list[int | None]:
        """
        Read all tile values in a block.
        
        Args:
            block: Block index (0-8)
            
        Returns:
            List of 9 values (1-9 or None for empty tiles) in reading order
            (left to right, top to bottom within the block)
        """
        # Calculate the top-left corner of the block
        block_row = (block // 3) * 3
        block_col = (block % 3) * 3
        
        result = []
        # Read all 9 tiles in the block in reading order
        for row in range(block_row, block_row + 3):
            for col in range(block_col, block_col + 3):
                result.append(self.grid[row][col].value)
        
        return result

    def readBoard(self) -> list[list[int | None]]:
        """
        Read the entire board as a 2D array of values.

        Returns:
            A 9x9 list of lists containing tile values (1-9 or None for empty tiles)
        """
        return [[self.grid[r][c].value for c in range(9)] for r in range(9)]